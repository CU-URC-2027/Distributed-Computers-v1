"""Nonblocking serial adapter; no motion decisions or background retransmission."""
import time
import serial
from .protocol import Decoder, encode


class SerialArduinoPort:
    def __init__(self, path, baud=115200, clock=time.monotonic):
        self.path, self.baud, self.clock = path, baud, clock
        self.serial = None
        self.decoder = Decoder()
        self.pending = b''
        self.pending_at = 0.
        self.retry_at = 0.
        self.error = ''

    @property
    def connected(self):
        return self.serial is not None

    def _disconnect(self, reason):
        if self.serial:
            self.serial.close()
        self.serial = None
        self.pending = b''
        self.decoder.reset()
        self.retry_at = self.clock() + 2.
        self.error = str(reason)

    def _write(self):
        if not self.pending:
            return
        if self.clock() - self.pending_at >= .1:
            raise serial.SerialException('Transmit frame expired')
        n = self.serial.write(self.pending)
        self.pending = self.pending[n:]

    def poll(self):
        try:
            if self.serial is None:
                if self.clock() < self.retry_at:
                    return []
                self.serial = serial.Serial(self.path, self.baud, timeout=0, write_timeout=0, exclusive=True)
                self.serial.reset_input_buffer()
                self.serial.reset_output_buffer()
                self.decoder.reset()
                # Terminate a possibly truncated frame left in the peer before reconnect.
                self.pending, self.pending_at = b'\n', self.clock()
                self.error = ''
            self._write()
            return self.decoder.feed(self.serial.read(min(4096, self.serial.in_waiting)))
        except (OSError, serial.SerialException, ValueError) as exc:
            self._disconnect(exc)
            return []

    def send(self, packet):
        if not self.connected or self.pending:
            return False  # Never queue or replace part of a frame.
        try:
            self.pending, self.pending_at = encode(packet), self.clock()
            self._write()
            return True
        except (OSError, serial.SerialException, ValueError) as exc:
            self._disconnect(exc)
            return False

    def close(self):
        self._disconnect('Closed')
