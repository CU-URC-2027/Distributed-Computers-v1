"""Exercise launch event wiring without starting a GUI, node, or physical link."""
import pytest

from launch import LaunchContext
from launch.actions import ExecuteProcess
from launch.events.process import ProcessStarted
from launch.utilities import perform_substitutions

from mini_rover_inspection.launching import inspection_handler, inspection_launch_arguments


def configured_context(**overrides):
    context = LaunchContext()
    for argument in inspection_launch_arguments():
        argument.execute(context)
    context.launch_configurations['namespace'] = '/test_rover'
    context.launch_configurations.update(overrides)
    return context


def started(action, pid=42817):
    return ProcessStarted(action=action, name='test-node', cmd=['/bin/true'],
                          cwd=None, env=None, pid=pid)


def application_arguments(node, context):
    """Resolve application args without resolving or executing the ROS binary."""
    result = []
    for token in node.cmd[1:]:
        value = perform_substitutions(context, token)
        if value == '--ros-args':
            break
        result.append(value)
    return result


@pytest.mark.parametrize('dashboard', ['false', 'true'])
def test_disabled_inspection_starts_no_companion_even_when_dashboard_requested(dashboard):
    process = ExecuteProcess(cmd=['/bin/true'])
    context = configured_context(inspection_dashboard=dashboard)
    handler = inspection_handler(process).event_handler
    assert handler.matches(started(process))
    assert handler.handle(started(process), context) == []


@pytest.mark.parametrize('role', ['c2', 'oc'])
def test_monitor_uses_exact_started_pid_and_deployment_settings(role):
    process = ExecuteProcess(cmd=['/bin/true'])
    unrelated_process = ExecuteProcess(cmd=['/bin/true'])
    context = configured_context(inspect_performance='true', namespace='/fleet/rover_two',
                                 inspect_interval='0.5', inspect_log_directory='/tmp/test logs')
    handler = inspection_handler(process, role=role).event_handler
    assert handler.matches(started(process))
    assert not handler.matches(started(unrelated_process))
    actions = handler.handle(started(process, pid=96271), context)
    assert len(actions) == 1
    assert actions[0].node_executable == 'performance_monitor'
    assert application_arguments(actions[0], context) == [
        '--role', role, '--pid', '96271', '--namespace', '/fleet/rover_two',
        '--interval', '0.5', '--log-directory', '/tmp/test logs',
    ]


def test_dashboard_requires_explicit_opt_in_and_uses_same_namespace():
    process = ExecuteProcess(cmd=['/bin/true'])
    context = configured_context(inspect_performance='true', inspection_dashboard='true',
                                 namespace='/fleet/rover_two')
    actions = inspection_handler(process).event_handler.handle(started(process), context)
    assert len(actions) == 2
    assert actions[1].node_executable == 'performance_dashboard'
    assert application_arguments(actions[1], context) == ['--namespace', '/fleet/rover_two']
