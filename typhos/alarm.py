"""
Module to define alarm summary frameworks and widgets.
"""
from functools import partial

from ophyd.device import Kind
from ophyd.signal import EpicsSignalBase
from pydm.widgets.channel import PyDMChannel
from pydm.widgets.drawing import (PyDMDrawingCircle,
                                  PyDMDrawingRectangle, PyDMDrawingTriangle,
                                  PyDMDrawingEllipse, PyDMDrawingPolygon)
from qtpy import QtCore

from .plugins import register_signal
from .utils import (channel_from_signal, get_all_signals_from_device,
                    TyphosObject)
from .widgets import HappiChannel


class KindLevel:
    """QT Enum for Ophyd Kind properties."""
    HINTED = 0
    NORMAL = 1
    CONFIG = 2
    OMITTED = 3


class AlarmLevel:
    """QT Enum for Typhos Alarm levels."""
    NO_ALARM = 0
    MINOR = 1
    MAJOR = 2
    INVALID = 3
    DISCONNECTED = 4


# Define behavior for the user's Kind selection.
KIND_FILTERS = {
    KindLevel.HINTED:
        (lambda walk: walk.item.kind == Kind.hinted),
    KindLevel.NORMAL:
        (lambda walk: walk.item.kind in (Kind.hinted, Kind.normal)),
    KindLevel.CONFIG:
        (lambda walk: walk.item.kind != Kind.omitted),
    KindLevel.OMITTED:
        (lambda walk: True),
    }


class TyphosAlarmBase(TyphosObject):
    """
    Class that holds logic and routines common to all Typhos Alarm widgets.

    Overall, these classes exist to summarize alarm states from Ophyd Devices
    and change the colors on indicator widgets appropriately.

    We will consider a subset of the signals that is of KindLevel and above and
    summarize state based on the "worst" alarm we see as defined by AlarmLevel.

    This cannot be a QWidget because pyqt does not support multiple
    inheritance. In that sense, this is more of a mix-in/interface class than a
    traditional base class, and it will not work if instantiated on its own.

    Below, we generate a bunch of TyphosAlarmBase subclasses via
    :func:`create_alarm_widget_cls`.
    """
    def __init__(self, *args, **kwargs):
        self._kind_level = KindLevel.HINTED
        super().__init__(*args, **kwargs)
        self.init_alarm_state()
        self.alarm_changed.connect(self.set_alarm_color)

    def init_alarm_state(self):
        self.addr_connected = {}
        self.addr_severity = {}
        self.addr_channels = {}
        self.device_channels = {}
        self.alarm_summary = AlarmLevel.DISCONNECTED
        self.set_alarm_color(AlarmLevel.DISCONNECTED)

    def channels(self):
        """
        Let pydm know about our pydm channels.
        """
        ch = []
        for lst in self.device_channels.values():
            ch.extend(lst)
        return ch

    def add_device(self, device):
        """
        Initialize our alarm handling when adding a device.
        """
        super().add_device(device)
        self.setup_alarm_config(device)

    def clear_all_alarm_configs(self):
        """
        Reset this widget down to the "no alarm handling" state.
        """
        channels = self.addr_channels.values()
        for ch in channels:
            ch.disconnect()
        self.init_alarm_state()

    def setup_alarm_config(self, device):
        """
        Add a device to the alarm summary.

        This will pick PVs based on the device kind and the configured kind
        level, configuring the PyDMChannels to update our alarm state and
        color when we get updates from our PVs.
        """
        sigs = get_all_signals_from_device(
            device,
            filter_by=KIND_FILTERS[self._kind_level]
            )
        channel_addrs = [channel_from_signal(sig) for sig in sigs]
        for sig in sigs:
            if not isinstance(sig, EpicsSignalBase):
                register_signal(sig)
        channels = [
            PyDMChannel(
                address=addr,
                connection_slot=partial(self.update_connection, addr=addr),
                severity_slot=partial(self.update_severity, addr=addr),
                )
            for addr in channel_addrs]

        self.device_channels[device.name] = channels
        for ch in channels:
            self.addr_channels[ch.address] = ch
            self.addr_connected[ch.address] = False
            self.addr_severity[ch.address] = AlarmLevel.INVALID
            ch.connect()

    def update_alarm_config(self):
        """
        Clean up the existing alarm config and create a new one.

        This must be called when settings like KindLevel are changed so we can
        re-evaluate them.
        """
        self.clear_all_alarm_configs()
        for dev in self.devices:
            self.setup_alarms(dev)

    def update_connection(self, connected, addr):
        """Slot that will be called when a PV connects or disconnects."""
        self.addr_connected[addr] = connected
        self.update_current_alarm()

    def update_severity(self, severity, addr):
        """Slot that will be called when a PV's alarm severity changes."""
        self.addr_severity[addr] = severity
        self.update_current_alarm()

    def update_current_alarm(self):
        """
        Check what the current worst available alarm state is.

        If the alarm state is different than the last time we checked,
        emit the "alarm_changed" signal. This signal is configured at
        init to change the color of this widget.
        """
        if not all(self.addr_connected.values()):
            new_alarm = AlarmLevel.DISCONNECTED
        else:
            new_alarm = max(self.addr_severity.values())
        if new_alarm != self.alarm_summary:
            self.alarm_changed.emit(new_alarm)
        self.alarm_summary = new_alarm

    def set_alarm_color(self, alarm_level):
        """
        Change the alarm color to the shade defined by the current alarm level.
        """
        self.setStyleSheet(indicator_stylesheet(self.shape_cls, alarm_level))


def indicator_stylesheet(shape_cls, alarm):
    """
    Create the indicator stylesheet that will modify a PyDMDrawing's color.

    Parameters
    ----------
    shape_cls : str
        The name of the PyDMDrawing widget subclass.

    alarm : int
        The value from AlarmLevel

    Returns
    -------
    indicator_stylesheet : str
        The correctly colored stylesheet to apply to the widget.
    """
    base = (
        f'{shape_cls} '
        '{border: none; '
        ' background: transparent;'
        ' qproperty-penColor: black;'
        ' qproperty-penWidth: 2;'
        ' qproperty-penStyle: SolidLine;'
        ' qproperty-brush: rgba'
        )

    if alarm is AlarmLevel.DISCONNECTED:
        return base + '(255,255,255,255);}'
    elif alarm is AlarmLevel.NO_ALARM:
        return base + '(0,255,0,255);}'
    elif alarm is AlarmLevel.MINOR:
        return base + '(255,255,0,255);}'
    elif alarm is AlarmLevel.MAJOR:
        return base + '(255,0,0,255);}'
    elif alarm is AlarmLevel.INVALID:
        return base + '(255,0,255,255);}'
    else:
        raise ValueError(f'Recieved invalid alarm level {alarm}')


def create_alarm_widget_cls(pydm_drawing_widget_cls):
    """
    Create a working alarm widget class based on a PyDM drawing widget.

    This is used to semi-automatically create a bunch of alarm shape widgets
    based on stock widgets from pydm.

    There are a few boilerplate definitions we need to add specifically inside
    of a QWidget subclass, and a shape_cls paramter to set so we can choose the
    correct class inside of our indicator stylesheet.

    Parameters
    ----------
    pydm_drawing_widget_cls : type
        The class we want to use as a base

    Returns
    -------
    alarm_widget_cls : type
        The class that will work as a Typhos alarm widget. This will be named
        f"TyphosAlarm{shape}", where shape is pulled from the PyDM class name.
    """
    drawing_widget_cls_name = pydm_drawing_widget_cls.__name__
    shape = drawing_widget_cls_name.split('PyDMDrawing')[1]
    alarm_widget_name = 'TyphosAlarm' + shape

    class TyphosAlarm(TyphosAlarmBase, pydm_drawing_widget_cls):
        QtCore.Q_ENUMS(KindLevel)
        QtCore.Q_ENUMS(AlarmLevel)

        alarm_changed = QtCore.Signal(AlarmLevel)
        shape_cls = drawing_widget_cls_name

        @QtCore.Property(KindLevel)
        def kindLevel(self):
            """
            Determines which signals to include in the alarm summary.

            If this is "hinted", only include hinted signals.
            If this is "normal", include normal and hinted signals.
            If this is "config", include everything except for omitted signals
            If this is "omitted", include all signals
            """
            return self._kind_level

        @kindLevel.setter
        def kindLevel(self, kind_level):
            # We must update the alarm config to add/remove PVs as appropriate.
            self._kind_level = kind_level
            self.update_alarm_config()

        @QtCore.Property(str)
        def channel(self):
            """The channel address to use for this widget"""
            if self._channel:
                return str(self._channel)
            return None

        @channel.setter
        def channel(self, value):
            if self._channel != value:
                # Remove old connection
                if self._channels:
                    self._channels.clear()
                    for channel in self._channels:
                        if hasattr(channel, 'disconnect'):
                            channel.disconnect()
                # Load new channel
                self._channel = str(value)
                channel = HappiChannel(address=self._channel,
                                       tx_slot=self._tx)
                self._channels = [channel]
                # Connect the channel to the HappiPlugin
                if hasattr(channel, 'connect'):
                    channel.connect()

        def _tx(self, value):
            """Receive information from happi channel"""
            self.add_device(value['obj'])


    return type(alarm_widget_name, (TyphosAlarm,), {})


# Explicitly create the classes one by one for clarity and readability
TyphosAlarmCircle = create_alarm_widget_cls(PyDMDrawingCircle)
TyphosAlarmRectangle = create_alarm_widget_cls(PyDMDrawingRectangle)
TyphosAlarmTriangle = create_alarm_widget_cls(PyDMDrawingTriangle)
TyphosAlarmEllipse = create_alarm_widget_cls(PyDMDrawingEllipse)
TyphosAlarmPolygon = create_alarm_widget_cls(PyDMDrawingPolygon)
