"""SDL game-controller mapping shared by Windows and Linux; no motor output."""
import os


def signed_axis(value):
    """SDL uses an asymmetric signed 16-bit range."""
    return max(-1.0, min(1.0, value / (32768.0 if value < 0 else 32767.0)))


class Xbox:
    def __init__(self, throttle_axis=None, steering_axis=None, hold_button=None):
        os.environ["SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS"] = "1"
        import pygame
        from pygame._sdl2 import controller
        self.pg, self.api = pygame, controller
        pygame.display.init()
        pygame.joystick.init()
        controller.init()
        self.device = None
        self.raw = throttle_axis is not None
        self.throttle_axis, self.steering_axis, self.hold_button = throttle_axis, steering_axis, hold_button
        if self.raw and (steering_axis is None or hold_button is None):
            raise ValueError("Raw mapping requires all three indices")
        self.stick = "Left"
        self.name = "Waiting for controller"
        self.axes = ()
        self.mapping = "SDL mapped sticks / LB hold"

    def read(self):
        pg = self.pg
        for event in pg.event.get():
            if event.type == pg.JOYDEVICEREMOVED and self.device is not None:
                joystick = self.device if self.raw else self.device.as_joystick()
                if event.instance_id == joystick.get_instance_id():
                    self.device = None
                    raise RuntimeError("Xbox disconnected; reconnect then enable again")
        if self.device is None:
            if self.raw:
                if not pg.joystick.get_count():
                    raise RuntimeError("No gamepad detected; check USB/Bluetooth connection")
                self.device = pg.joystick.Joystick(0)
            else:
                indices = [i for i in range(self.api.get_count()) if self.api.is_controller(i)]
                if not indices:
                    raise RuntimeError("No mapped gamepad detected; check USB/Bluetooth connection")
                self.device = self.api.Controller(indices[0])
        if self.raw:
            self.name = self.device.get_name()
            self.axes = tuple(self.device.get_axis(i) for i in range(self.device.get_numaxes()))
            if max(self.throttle_axis, self.steering_axis) >= len(self.axes) or self.hold_button >= self.device.get_numbuttons():
                raise RuntimeError("Raw mapping exceeds this controller's axes/buttons")
            self.mapping = f"Raw axes {self.throttle_axis}/{self.steering_axis}, button {self.hold_button}"
            return -self.axes[self.throttle_axis], self.axes[self.steering_axis], bool(self.device.get_button(self.hold_button))
        if not self.device.attached():
            self.device = None
            raise RuntimeError("Xbox disconnected")
        self.name = self.device.name
        self.axes = tuple(signed_axis(self.device.get_axis(a)) for a in (
            pg.CONTROLLER_AXIS_LEFTX, pg.CONTROLLER_AXIS_LEFTY,
            pg.CONTROLLER_AXIS_RIGHTX, pg.CONTROLLER_AXIS_RIGHTY))
        offset = 0 if self.stick == "Left" else 2
        self.mapping = f"{self.stick} stick / LB hold; axes LX LY RX RY"
        return -self.axes[offset + 1], self.axes[offset], bool(self.device.get_button(pg.CONTROLLER_BUTTON_LEFTSHOULDER))

    def close(self):
        if self.device is not None:
            self.device.quit()
            self.device = None
        self.api.quit()
        self.pg.joystick.quit()
        self.pg.display.quit()
