#!/usr/bin/env python3
"""Launch the existing C2 GUI with a configured node name; no workspace edits."""
import argparse
import signal
import threading
import tkinter as tk
import rclpy
from rclpy.executors import SingleThreadedExecutor
from mini_rover_c2.ros_client import C2Node
from mini_rover_c2.gui import RosStation


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--namespace', required=True); p.add_argument('--node-name', required=True)
    args=p.parse_args(); rclpy.init(args=[])
    node=C2Node(namespace=args.namespace, cli_args=['--ros-args','-r','__node:='+args.node_name], use_global_arguments=False)
    executor=SingleThreadedExecutor(); executor.add_node(node)
    def spin():
        try: executor.spin()
        except rclpy.executors.ExternalShutdownException: pass
    thread=threading.Thread(target=spin,daemon=True); thread.start(); app=None
    try:
        app=RosStation(tk.Tk(),args,node)
        signal.signal(signal.SIGTERM,lambda *_:app.root.after(0,app.close))
        app.root.mainloop()
    finally:
        if app and not app._closing: app.close()
        executor.shutdown(timeout_sec=1); thread.join(timeout=1); node.destroy_node()
        if rclpy.ok():rclpy.shutdown()


if __name__=='__main__': main()
