"""Manual OPEN_LOOP_PWM dashboard. Only a held mouse control requests motion."""
import argparse
import threading
import time
import tkinter as tk
from tkinter import ttk
import rclpy
from rclpy.executors import SingleThreadedExecutor
from mini_rover_control.contracts import WHEELS, MODE
from .ros_client import C2Node


class RosStation:
    def __init__(self, root, args, node):
        self.root, self.ros = root, node
        self._closing = False
        self._loop_after = self._focus_after = None
        self._mouse_hold = False
        self.numeric_throttle = tk.StringVar(root, value='0.0')
        self.numeric_steering = tk.StringVar(root, value='0.0')
        self.drop = tk.BooleanVar(root, value=False)
        root.title('Mini Rover C2 | Manual hold-to-drive')
        root.geometry('1120x820')
        panel = ttk.Frame(root, padding=18)
        panel.pack(fill='both', expand=True)
        ttk.Label(panel, text='OPEN_LOOP_PWM • manual control', font=('', 18, 'bold')).pack(anchor='w')
        self.connection_banner = ttk.Label(panel, text='Waiting for controller provenance', wraplength=1060)
        self.connection_banner.pack(anchor='w', pady=8)
        self.status = ttk.Label(panel, text='MCU state: UNKNOWN', wraplength=1060)
        self.status.pack(anchor='w', pady=8)
        controls = ttk.Frame(panel)
        controls.pack(fill='x', pady=6)
        for text, command in (('Connect / claim OC', lambda: self.action(self.ros.connect)),
                              ('Enable at neutral', self.enable), ('Disarm', self.disarm),
                              ('Reset STOP', self.reset_stop), ('STOP', self.stop)):
            ttk.Button(controls, text=text, command=command).pack(side='left', padx=4)
        form = ttk.LabelFrame(panel, text='Normalized input [-1, 1]', padding=12)
        form.pack(fill='x', pady=8)
        for label, variable in (('Throttle: + forward / − reverse', self.numeric_throttle),
                                ('Steering: + left / − right', self.numeric_steering)):
            row = ttk.Frame(form)
            row.pack(fill='x', pady=5)
            ttk.Label(row, text=label, width=40).pack(side='left')
            ttk.Entry(row, textvariable=variable, width=16).pack(side='left')
        self.hold_button = ttk.Button(form, text='HOLD TO DRIVE — release to zero')
        self.hold_button.pack(anchor='w', pady=10)
        self.hold_button.bind('<ButtonPress-1>', self.hold_start)
        self.hold_button.bind('<ButtonRelease-1>', self.hold_end)
        self.hold_button.bind('<Leave>', self.hold_end)
        root.bind('<ButtonRelease-1>', self.hold_end, add='+')
        root.bind('<FocusOut>', self.focus_out, add='+')
        ttk.Label(form, text='Enable with both fields at zero and hold released. Enter values, then hold to drive.\n'
            'Release requests zero. Leaving the control or losing window focus ends motion.\n'
            'PWM is capped at 63/255; it is not a measured speed. Each side drives its front and rear wheels.',
            wraplength=1040).pack(anchor='w')
        ttk.Checkbutton(form, text='Pause command publication (watchdog test)', variable=self.drop).pack(anchor='w', pady=5)
        self.oc_details = ttk.Label(panel, text='', wraplength=1060)
        self.oc_details.pack(anchor='w', pady=10)
        self.pwm_status = ttk.Label(panel, text='Requested / limited / MCU-applied PWM: N/A', wraplength=1060)
        self.pwm_status.pack(anchor='w', pady=8)
        columns = ('wheel', 'count', 'valid', 'direction')
        self.wheel_table = ttk.Treeview(panel, columns=columns, show='headings', height=4)
        for column, title in zip(columns, ('Wheel', 'Raw pulse count', 'Measurement valid', 'Direction known')):
            self.wheel_table.heading(column, text=title)
            self.wheel_table.column(column, width=220, anchor='center')
        for name in WHEELS:
            self.wheel_table.insert('', 'end', iid=name, values=(name, 'N/A', 'No', 'No'))
        self.wheel_table.pack(fill='x', pady=10)
        self.fault_status = ttk.Label(panel, text='Faults: waiting', wraplength=1060)
        self.fault_status.pack(anchor='w', pady=5)
        self.hint = ttk.Label(panel, text=self.ros.notice, wraplength=1060)
        self.hint.pack(anchor='w', pady=8)
        self._last_notice = self._notice_key()
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.loop()

    def action(self, callback):
        try:
            callback()
            self.hint.config(text=self.ros.notice)
        except (ValueError, OSError) as exc:
            self.hint.config(text=str(exc))
        finally:
            self._last_notice = self._notice_key()

    def _notice_key(self):
        return self.ros.notice, getattr(self.ros, 'notice_revision', 0)

    def update_notice(self):
        current = self._notice_key()
        if current != self._last_notice:
            self.hint.config(text=self.ros.notice)
            self._last_notice = current

    def enable(self):
        def enable():
            if self._mouse_hold or float(self.numeric_throttle.get()) or float(self.numeric_steering.get()):
                raise ValueError('Set both inputs to zero and release hold before enabling')
            self.ros.operation('enable')
        self.action(enable)

    def apply_numeric(self):
        self.action(lambda: self.ros.controller.apply(float(self.numeric_throttle.get()), float(self.numeric_steering.get())))

    def hold_start(self, event=None):
        self._mouse_hold = True
        def start():
            model = self.ros.controller
            model.apply(float(self.numeric_throttle.get()), float(self.numeric_steering.get()))
            model.begin_hold()
            self._mouse_hold = True
            self.ros.notice = 'Hold active — releasing requests zero'
        self.action(start)
        return 'break'

    def hold_end(self, event=None):
        self._mouse_hold = False
        self.ros.controller.release()
        return 'break' if event is not None else None

    def disarm(self):
        self._mouse_hold = False
        self.ros.controller.disarm()
        self.action(lambda: self.ros.operation('disable'))

    def stop(self):
        self._mouse_hold = False
        self.ros.controller.stop()
        self.action(lambda: self.ros.operation('stop'))

    def reset_stop(self):
        self._mouse_hold = False
        self.action(lambda: self.ros.operation('reset'))

    def focus_out(self, event=None):
        if not self._closing and self._focus_after is None:
            self._focus_after = self.root.after_idle(self.check_focus)

    def check_focus(self):
        self._focus_after = None
        if not self._closing and not self.root.tk.call('focus', '-displayof', self.root._w):
            if self.ros.controller.enable_requested or self._mouse_hold:
                self.disarm()

    @staticmethod
    def fault_text(fault):
        return (f"{fault['source']}:{fault['code']} ({fault['severity']}, command {fault['command_id']}, "
                f"source-clock time {fault['time_ms']} ms)")

    def loop(self):
        if self._closing:
            return
        self._loop_after = None
        if not rclpy.ok():
            self.close()
            return
        if self._mouse_hold:
            self.check_focus()
        self.ros.pump(publish=not self.drop.get())
        self.update_notice()
        model, state = self.ros.controller, self.ros.controller.state
        fresh = model.fresh()
        actual = ('ENABLED' if state['actual_enabled'] else 'DISABLED') if state and fresh and state['enabled_known'] else 'UNKNOWN / STALE'
        authorized = str(bool(state['oc_authorized'])) if state and fresh else 'UNKNOWN / STALE'
        self.status.config(text=f'OC authorized: {authorized} | MCU confirmed: {actual} | Enable requested: {model.enable_requested} | Hold: {model.holding}')
        chassis = self.ros.chassis
        if chassis:
            provenance = 'SIMULATION — no physical motion evidence' if chassis.simulated else 'HARDWARE — USB MCU feedback'
            self.connection_banner.config(text=f'C2 → ROS 2 OC → {provenance} | {MODE}')
        if state:
            oc_age = (time.monotonic()-model.state_at)*1000
            self.oc_details.config(text=f"C2 process: {model.client_id} | Owner: {state['session_id'] or 'none'}\n"
                f"Accepted C2 ID: {state['last_accepted_command_id']} | MCU-applied C2 ID: {state['last_applied_command_id']} "
                f"(session {state['applied_session_id'] or 'none'}, transport {state['last_applied_seq']})\n"
                f"OC receipt age: {oc_age:.0f} ms | MCU age at OC: {state['feedback_age_ms']:.0f} ms | "
                f"Sensor age lower bound: {self.ros.chassis_age_ms():.0f} ms; transit delay unknown\nReason: {state['reason']}")
            requested = f"L {state['requested_left_pwm']:.1f}, R {state['requested_right_pwm']:.1f}"
            limited = f"L {state['limited_left_pwm']}, R {state['limited_right_pwm']}"
            applied = f'L {chassis.applied_left_pwm}, R {chassis.applied_right_pwm}' if self.ros.chassis_fresh() else 'N/A — stale/unknown'
            state_label = '' if fresh else ' (last known, stale)'
            self.pwm_status.config(text=f"OC requested{state_label}: {requested} | OC limited{state_label}: {limited} | MCU applied: {applied}\n"
                f"Limit: {state['limit_reason'] or 'none'} | Counts are sensor evidence; an applied acknowledgment does not prove motion.")
            current = '; '.join(self.fault_text(f) for f in state['faults']) or 'none'
            first = (self.fault_text(state['first_fault']) +
                f"; source session {state['first_fault_session_id'] or 'none'}") if state['first_fault_valid'] else 'none'
            self.fault_status.config(text=f'Current faults: {current}\nFirst fault: {first}')
        valid_sample = self.ros.chassis_fresh()
        for name in WHEELS:
            wheel = getattr(chassis, name) if chassis else None
            valid = bool(wheel and valid_sample and wheel.measurement_valid)
            self.wheel_table.item(name, values=(name, str(wheel.raw_count) if valid else 'N/A',
                'Yes' if valid else 'No', 'Yes' if valid and wheel.direction_valid else 'No'))
        self._loop_after = self.root.after(50, self.loop)

    def close(self):
        if self._closing:
            return
        self._closing = True
        self.ros.controller.disarm()
        if rclpy.ok():
            try:
                self.ros.operation('disable')
                self.ros.pump()
            except (ValueError, RuntimeError):
                pass
        for callback in (self._loop_after, self._focus_after):
            if callback:
                self.root.after_cancel(callback)
        self.root.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--namespace', default='/rover1')
    args = parser.parse_args(argv)
    rclpy.init(args=[])
    node = C2Node(namespace=args.namespace)
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    def spin():
        try:
            executor.spin()
        except rclpy.executors.ExternalShutdownException:
            pass
    thread = threading.Thread(target=spin, daemon=True)
    thread.start()
    app = None
    try:
        app = RosStation(tk.Tk(), args, node)
        app.root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        if app and not app._closing:
            app.close()
        executor.shutdown(timeout_sec=1)
        thread.join(timeout=1)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
