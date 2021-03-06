import ctypes
import patroni.watchdog.linux as linuxwd
import sys
import unittest

from mock import patch, Mock, PropertyMock
from patroni.watchdog import Watchdog, WatchdogError
from patroni.watchdog.base import NullWatchdog
from patroni.watchdog.linux import LinuxWatchdogDevice


class MockDevice(object):
    def __init__(self, fd, filename, flag):
        self.fd = fd
        self.filename = filename
        self.flag = flag
        self.timeout = 60
        self.open = True
        self.writes = []


mock_devices = [None]


def mock_open(filename, flag):
    fd = len(mock_devices)
    mock_devices.append(MockDevice(fd, filename, flag))
    return fd


def mock_ioctl(fd, op, arg=None, mutate_flag=False):
    assert 0 < fd < len(mock_devices)
    dev = mock_devices[fd]
    sys.stderr.write("Ioctl %d %d %r\n" % (fd, op, arg))
    if op == linuxwd.WDIOC_GETSUPPORT:
        sys.stderr.write("Get support\n")
        assert(mutate_flag is True)
        arg.options = sum(map(linuxwd.WDIOF.get, ['SETTIMEOUT', 'KEEPALIVEPING']))
        arg.identity = (ctypes.c_ubyte*32)(*map(ord, 'Mock Watchdog'))
    elif op == linuxwd.WDIOC_GETTIMEOUT:
        arg.value = dev.timeout
    elif op == linuxwd.WDIOC_SETTIMEOUT:
        sys.stderr.write("Set timeout called with %s\n" % arg.value)
        assert 0 < arg.value < 65535
        dev.timeout = arg.value - 1
    else:
        raise Exception("Unknown op %d", op)
    return 0


def mock_write(fd, string):
    assert 0 < fd < len(mock_devices)
    assert len(string) == 1
    assert mock_devices[fd].open
    mock_devices[fd].writes.append(string)


def mock_close(fd):
    assert 0 < fd < len(mock_devices)
    assert mock_devices[fd].open
    mock_devices[fd].open = False


@patch('os.open', mock_open)
@patch('os.write', mock_write)
@patch('os.close', mock_close)
@patch('fcntl.ioctl', mock_ioctl)
class TestWatchdog(unittest.TestCase):
    def setUp(self):
        mock_devices[:] = [None]

    @patch('platform.system', Mock(return_value='Linux'))
    @patch.object(LinuxWatchdogDevice, 'can_be_disabled', PropertyMock(return_value=True))
    def test_unsafe_timeout_disable_watchdog_and_exit(self):
        self.assertRaises(SystemExit, Watchdog({'ttl': 30, 'loop_wait': 15, 'watchdog': {'mode': 'required'}}).activate)

    @patch('platform.system', Mock(return_value='Linux'))
    @patch.object(LinuxWatchdogDevice, 'get_timeout', Mock(return_value=16))
    def test_timeout_does_not_ensure_safe_termination(self):
        Watchdog({'ttl': 30, 'loop_wait': 15, 'watchdog': {'mode': 'auto'}}).activate()
        self.assertEquals(len(mock_devices), 2)

    @patch('platform.system', Mock(return_value='Linux'))
    @patch.object(Watchdog, 'is_running', PropertyMock(return_value=False))
    def test_watchdog_not_activated(self):
        self.assertRaises(SystemExit, Watchdog({'ttl': 30, 'loop_wait': 10, 'watchdog': {'mode': 'required'}}).activate)

    @patch('platform.system', Mock(return_value='Linux'))
    def test_basic_operation(self):
        watchdog = Watchdog({'ttl': 30, 'loop_wait': 10, 'watchdog': {'mode': 'required'}})
        watchdog.activate()

        self.assertEquals(len(mock_devices), 2)
        device = mock_devices[-1]
        self.assertTrue(device.open)

        self.assertEquals(device.timeout, 14)

        watchdog.keepalive()
        self.assertEquals(len(device.writes), 1)

        watchdog.disable()
        self.assertFalse(device.open)
        self.assertEquals(device.writes[-1], b'V')

    def test_invalid_timings(self):
        watchdog = Watchdog({'ttl': 30, 'loop_wait': 20, 'watchdog': {'mode': 'automatic'}})
        watchdog.activate()
        self.assertEquals(len(mock_devices), 1)
        self.assertFalse(watchdog.is_running)

    def test_parse_mode(self):
        with patch('patroni.watchdog.base.logger.warning', new_callable=Mock()) as warning_mock:
            watchdog = Watchdog({'ttl': 30, 'loop_wait': 10, 'watchdog': {'mode': 'bad'}})
            self.assertEquals(watchdog.mode, 'off')
            warning_mock.assert_called_once()

    @patch('platform.system', Mock(return_value='Unknown'))
    def test_unsupported_platform(self):
        self.assertRaises(SystemExit, Watchdog, {'ttl': 30, 'loop_wait': 10, 'watchdog': {'mode': 'required'}})

    def test_exceptions(self):
        wd = Watchdog({'ttl': 30, 'loop_wait': 10, 'watchdog': {'mode': 'bad'}})
        wd.impl.close = wd.impl.keepalive = Mock(side_effect=WatchdogError(''))
        self.assertIsNone(wd.disable())
        self.assertIsNone(wd.keepalive())


class TestNullWatchdog(unittest.TestCase):

    def test_basics(self):
        watchdog = NullWatchdog()
        self.assertTrue(watchdog.can_be_disabled)
        self.assertRaises(WatchdogError, watchdog.set_timeout, 1)
        self.assertEquals(watchdog.describe(), 'NullWatchdog')
        self.assertIsInstance(NullWatchdog.from_config({}), NullWatchdog)


class TestLinuxWatchdogDevice(unittest.TestCase):

    def setUp(self):
        self.impl = LinuxWatchdogDevice.from_config({})

    @patch('os.open', Mock(return_value=3))
    @patch('os.write', Mock(side_effect=OSError))
    @patch('fcntl.ioctl', Mock(return_value=0))
    def test_basics(self):
        self.impl.open()
        try:
            if self.impl.get_support().has_foo:
                self.assertFail()
        except Exception as e:
            self.assertTrue(isinstance(e, AttributeError))
        self.assertRaises(WatchdogError, self.impl.close)
        self.assertRaises(WatchdogError, self.impl.keepalive)
        self.assertRaises(WatchdogError, self.impl.set_timeout, -1)

    @patch('os.open', Mock(return_value=3))
    @patch('fcntl.ioctl', Mock(return_value=-1))
    def test__ioctl(self):
        self.assertRaises(WatchdogError, self.impl.get_support)
        self.impl.open()
        self.assertRaises(IOError, self.impl.get_support)
