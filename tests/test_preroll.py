"""预录真实状态/队列回归；桩只替换设备、窗口和 UI，不启动麦克风或联网。"""
import asyncio
import importlib.util
import logging
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def module(name, **attrs):
    result = types.ModuleType(name)
    result.__dict__.update(attrs)
    return result


class State:
    def __init__(self):
        self.recording = False
        self.stream = None
        self.queue_in = asyncio.Queue()
        self.recording_start_time = 0

    def start_recording(self, start):
        self.recording = True
        self.recording_start_time = start

    def stop_recording(self):
        self.recording = False
        return 0.4


class SilentUI:
    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class PreRollTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = types.SimpleNamespace(pre_roll_enabled=True, pre_roll_seconds=0.1,
                                            mic_idle_release_sec=0, max_record_sec=0,
                                            mute_speaker_while_recording=False)
        logger = logging.getLogger('preroll-test')
        sd = module('sounddevice', CallbackFlags=object, PortAudioError=RuntimeError,
                    query_devices=lambda **kw: {'max_input_channels': 1, 'name': 'fake'},
                    _terminate=lambda: None, _initialize=lambda: None)

        class Device:
            def __init__(self, **kwargs):
                self.callback = kwargs['callback']

            def start(self):
                pass

            def close(self):
                pass

        sd.InputStream = Device
        mods = {
            'config_client': module('config_client', ClientConfig=self.config),
            'sounddevice': sd,
            'core.client.audio': module('core.client.audio', logger=logger,
                speaker_mute=SilentUI(), idle_release=SilentUI()),
            'core.client.shortcut': module('core.client.shortcut', logger=logger),
            'core.client.state': module('core.client.state', console=SilentUI()),
            'core.tools.my_status': module('core.tools.my_status', Status=SilentUI),
            'core.client.ui.recording_toast': module('core.client.ui.recording_toast',
                RecordingToast=SilentUI, ready_active=lambda: None),
            'core.tools.window_focus': module('core.tools.window_focus',
                activate_window_under_cursor=lambda: ''),
            'core.ui.recording_level': module('core.ui.recording_level', set_level=lambda v: None),
            'core.ui': module('core.ui', toast_recording=types.SimpleNamespace(cap_deadline=0)),
            'core.client': module('core.client', key_trace=types.SimpleNamespace(
                ralt=lambda: False, push=lambda *args: None)),
        }
        self.patcher = patch.dict(sys.modules, mods)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.stream_mod = self.load('core.client.audio.stream', 'core/client/audio/stream.py')
        self.task_mod = self.load('core.client.shortcut.task', 'core/client/shortcut/task.py')
        self.task_mod._repeat_gap = lambda: 1.2

    def load(self, name, path):
        spec = importlib.util.spec_from_file_location(name, ROOT / path)
        result = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(result)
        return result

    async def asyncSetUp(self):
        self.app = types.SimpleNamespace(state=State(), loop=asyncio.get_running_loop(), ws=None)
        self.stream = self.app.stream = self.stream_mod.AudioStreamManager(self.app)
        self.stream.start()

    def feed(self, value, frames=2400):
        data = np.full((frames, 1), value, dtype=np.float32)
        self.stream._audio_callback(data, frames, None, None)
        data[:] = -999  # PortAudio 会复用输入内存，缓冲必须拥有自己的副本

    async def drain(self, queue):
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        items = []
        while not queue.empty():
            items.append(queue.get_nowait())
            queue.task_done()
        return items

    async def test_begin_prefix_live_finish_order_and_no_duplicate(self):
        self.feed(1)
        self.feed(2)
        self.feed(3)
        queue = self.stream.begin_recording(100.0)
        self.feed(4)
        self.stream.end_recording()
        self.feed(5)
        items = await self.drain(queue)
        self.assertEqual([i['type'] for i in items], ['begin', 'data', 'data', 'data', 'finish'])
        self.assertEqual([i['data'][0, 0] for i in items if i['type'] == 'data'], [2, 3, 4])
        queue = self.stream.begin_recording(101.0)
        self.assertEqual([i['data'][0, 0] for i in await self.drain(queue) if i['type'] == 'data'], [5])

    async def test_partial_block_exact_sample_bound(self):
        self.feed(1, 3000)
        self.feed(2, 3000)
        queue = self.stream.begin_recording(100)
        audio = [i['data'] for i in await self.drain(queue) if i['type'] == 'data']
        self.assertEqual([len(a) for a in audio], [1800, 3000])
        self.assertEqual([a[0, 0] for a in audio], [1, 2])

    async def test_disabled_idle_release_stale_and_missing_defaults(self):
        for attr, value in [('pre_roll_enabled', False), ('mic_idle_release_sec', 1)]:
            self.feed(1)
            setattr(self.config, attr, value)
            self.feed(2)
            queue = self.stream.begin_recording(100)
            self.assertEqual([i['type'] for i in await self.drain(queue)], ['begin'])
            self.stream.end_recording(cancel=True)
            setattr(self.config, attr, True if attr == 'pre_roll_enabled' else 0)
        self.feed(3)
        self.stream._last_cb -= 2
        queue = self.stream.begin_recording(101)
        self.assertEqual([i['type'] for i in await self.drain(queue)], ['begin'])
        self.stream.end_recording(cancel=True)
        del self.config.pre_roll_enabled
        self.feed(4)
        self.assertFalse(self.stream._pre_roll)

    async def test_seconds_clamp_and_invalid_fallback(self):
        for seconds, expected in [(0, 4800), (99, 144000), (float('nan'), 24000),
                                  ('bad', 24000), (None, 24000), (True, 24000)]:
            self.config.pre_roll_seconds = seconds
            self.feed(7, 150000)
            queue = self.stream.begin_recording(100)
            audio = [i['data'] for i in await self.drain(queue) if i['type'] == 'data']
            self.assertEqual(sum(len(a) for a in audio), expected)
            self.stream.end_recording(cancel=True)
        del self.config.pre_roll_seconds
        self.feed(8, 30000)
        queue = self.stream.begin_recording(100)
        self.assertEqual(sum(len(i['data']) for i in await self.drain(queue) if i['type'] == 'data'), 24000)

    async def test_cancel_stop_reopen_and_explicit_clear(self):
        for action in [self.stream.clear_pre_roll, self.stream.stop, self.stream.reopen]:
            self.feed(1)
            old_callback = self.app.state.stream.callback
            action()
            old_callback(np.ones((2400, 1), dtype=np.float32), 2400, None, None)
            # clear_pre_roll 不换流，旧 callback 仍有效，单独清理一次。
            self.stream.clear_pre_roll()
            self.assertFalse(self.stream._pre_roll)
            self.stream.start()
        self.feed(2)
        queue = self.stream.begin_recording(100)
        self.feed(3)
        self.stream.end_recording(cancel=True)
        self.assertFalse(self.stream._pre_roll)
        next_queue = self.stream.begin_recording(101)
        self.assertEqual([i['type'] for i in await self.drain(next_queue)], ['begin'])
        self.assertIsNot(queue, next_queue)

    async def test_reopen_ignores_old_device_callback(self):
        old_callback = self.app.state.stream.callback
        self.feed(1)
        self.stream.reopen()
        old_callback(np.ones((2400, 1), dtype=np.float32), 2400, None, None)
        self.feed(2)
        queue = self.stream.begin_recording(100)
        self.assertEqual([i['data'][0, 0] for i in await self.drain(queue) if i['type'] == 'data'], [2])

    async def test_callback_and_begin_finish_are_serialized(self):
        self.feed(1)
        entered, release = threading.Event(), threading.Event()
        original = self.app.state.start_recording

        def delayed(start):
            entered.set()
            if not release.wait(2):
                raise AssertionError('transition timeout')
            original(start)

        self.app.state.start_recording = delayed
        starter = threading.Thread(target=self.stream.begin_recording, args=(100,))
        starter.start()
        self.assertTrue(entered.wait(2))
        callback = threading.Thread(target=self.feed, args=(2,))
        callback.start()
        release.set()
        starter.join(2)
        callback.join(2)
        self.assertFalse(starter.is_alive() or callback.is_alive())
        self.stream.end_recording()
        items = await self.drain(self.app.state.queue_in)
        self.assertEqual([i['type'] for i in items], ['begin', 'data', 'data', 'finish'])
        self.assertEqual([i['data'][0, 0] for i in items if i['type'] == 'data'], [1, 2])

    async def test_finish_waits_for_last_inflight_callback(self):
        queue = self.stream.begin_recording(100)
        entered, release = threading.Event(), threading.Event()

        class Block(np.ndarray):
            def copy(inner, *args, **kwargs):
                entered.set()
                if not release.wait(2):
                    raise AssertionError('callback timeout')
                return np.asarray(inner).copy(*args, **kwargs)

        data = np.ones((2400, 1), dtype=np.float32).view(Block)
        callback = threading.Thread(
            target=self.stream._audio_callback, args=(data, len(data), None, None))
        callback.start()
        self.assertTrue(entered.wait(2))
        finisher = threading.Thread(target=self.stream.end_recording)
        finisher.start()
        release.set()
        callback.join(2)
        finisher.join(2)
        self.assertFalse(callback.is_alive() or finisher.is_alive())
        self.assertEqual([i['type'] for i in await self.drain(queue)],
                         ['begin', 'data', 'finish'])


    async def test_real_recorder_cancel_and_slow_finish_isolate_next_session(self):
        sessions = []
        finishing = asyncio.Event()
        complete_finish = asyncio.Event()
        sent = []

        class Cloud:
            def feed(inner, data):
                pass

            def cancel(inner):
                pass

            async def finish(inner, timeout):
                finishing.set()
                await complete_finish.wait()
                return 'test'

        cloud = module('core.client.audio.cloud_asr', create=lambda preview: Cloud(),
                       finish_timeout=lambda: 1)
        agc = module('core.client.audio.agc', apply=lambda data, gain: data)
        dependencies = {
            'websockets': module('websockets'),
            'core.client.voice_trace': module('core.client.voice_trace', event=lambda *a, **k: None),
            'core.client.voice_gate': module('core.client.voice_gate', vad_reasons=lambda *a: []),
            'core.tools.terms': module('core.tools.terms', load_terms=lambda: ''),
            'core.client.audio.file_manager': module('core.client.audio.file_manager', AudioFileManager=object),
            'core.client.connection': module('core.client.connection', WebSocketManager=object),
            'core.protocol': module('core.protocol', AudioMessage=types.SimpleNamespace),
            'core.client.audio.decimate': module('core.client.audio.decimate',
                Decimator3=lambda: types.SimpleNamespace(process=lambda data: data[::3])),
            'core.client.audio.level': module('core.client.audio.level',
                noise_voice=lambda blocks: (-60, -10), is_silence=lambda *args: False),
            'core.client.audio.cloud_asr': cloud,
            'core.client.audio.agc': agc,
        }
        self.config.__dict__.update(asr_engine='test', save_audio=False, threshold=10000,
            silence_rms_gate=0, vad_enable=False, asr_agc=False, mic_seg_duration=60,
            mic_seg_overlap=4, context='', polish=False, polish_structure=False, language='zh')
        self.app.state.set_voice_level = lambda *args: None
        self.app.state.pop_audio_file = lambda *args: None

        async def send(message):
            sent.append(message)
            return True

        self.app.ws = types.SimpleNamespace(is_connected=True, send=send)
        audio_package = sys.modules['core.client.audio']
        audio_package.agc, audio_package.cloud_asr = agc, cloud
        sys.modules['core.client.ui.recording_toast'].preview_active = lambda *args: None
        with patch.dict(sys.modules, dependencies):
            real = self.load('core.client.audio.recorder', 'core/client/audio/recorder.py')

            class Recorder(real.AudioRecorder):
                def __init__(inner, app):
                    super().__init__(app)
                    sessions.append(inner)

            shortcut = types.SimpleNamespace(key='test', type='mouse', hold_mode=False,
                                             is_toggle_key=lambda: False)
            task = self.task_mod.ShortcutTask(self.app, shortcut, Recorder)
            task._guard = lambda *args: None
            async def wait_for_cache(session):
                async def ready():
                    while not session._cache:
                        await asyncio.sleep(0.01)
                await asyncio.wait_for(ready(), 2)

            task.launch()
            self.feed(1)
            await wait_for_cache(sessions[0])
            self.assertEqual(sessions[0]._cache[0][0, 0], 1)
            task.cancel()
            task.launch()  # 不等待真实 recorder 的 CancelledError 清理
            self.feed(2)
            await wait_for_cache(sessions[1])
            self.assertEqual(len(sessions[1]._cache), 1)
            self.assertEqual(sessions[1]._cache[0][0, 0], 2)
            self.assertIsNot(sessions[0].state.queue_in, sessions[1].state.queue_in)
            # 代理队列之外的状态写回真实 ClientState，不产生状态副本。
            sessions[1].state.last_level = {'test': True}
            self.assertEqual(self.app.state.last_level, {'test': True})
            task.finish()
            previous = task.task
            await asyncio.wait_for(finishing.wait(), 1)
            task.launch()  # 真实 recorder 仍在异步等待云端 finish
            self.feed(3)
            await wait_for_cache(sessions[2])
            self.assertEqual(len(sessions[2]._cache), 1)
            self.assertEqual(sessions[2]._cache[0][0, 0], 3)
            self.assertEqual(sessions[2]._start_time, task.recording_start_time)
            complete_finish.set()
            await asyncio.wrap_future(previous)
            task.cancel()
            await asyncio.sleep(0.01)
            self.assertFalse(self.app.state.recording)
            self.assertTrue(any(message.is_final for message in sent))


if __name__ == '__main__':
    unittest.main()
