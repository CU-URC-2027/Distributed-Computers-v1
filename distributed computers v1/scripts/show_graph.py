#!/usr/bin/env python3
"""Read-only rqt graph, scoped by namespace and refreshed every two seconds."""
import argparse
import re
from python_qt_binding.QtCore import QTimer
from rqt_graph.ros_graph import RosGraph
from rqt_gui.main import Main


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--namespace',required=True)
    args=p.parse_args()
    pattern=re.escape(args.namespace.rstrip('/'))+'/.*'
    original_init, original_restore, original_update=RosGraph.__init__,RosGraph.restore_settings,RosGraph._update_rosgraph
    def update(self):
        if not self._node.context.ok():
            if hasattr(self,'_dcv1_timer'):self._dcv1_timer.stop()
            return
        original_update(self)
    def initialize(self,context):
        original_init(self,context)
        self._dcv1_timer=QTimer(self._widget)
        self._dcv1_timer.timeout.connect(self._update_rosgraph);self._dcv1_timer.start(2000)
    def restore(self,plugin_settings,instance_settings):
        original_restore(self,plugin_settings,instance_settings)
        w=self._widget
        w.graph_type_combo_box.setCurrentIndex(2)
        w.filter_line_edit.setText(pattern);w.topic_filter_line_edit.setText(pattern)
        w.dead_sinks_check_box.setChecked(False);w.leaf_topics_check_box.setChecked(False)
        w.auto_fit_graph_check_box.setChecked(True);w.namespace_cluster_spin_box.setValue(1)
        self._update_rosgraph()
        w.window().resize(1200,720);w.window().show()
    RosGraph._update_rosgraph=update;RosGraph.__init__=initialize;RosGraph.restore_settings=restore
    return Main().main(['rqt_graph','--force-discover'],standalone='rqt_graph.ros_graph.RosGraph')


if __name__=='__main__':raise SystemExit(main())
