"""Launch a local observer against the exact process created by ROS launch."""
from launch.actions import DeclareLaunchArgument, RegisterEventHandler
from launch.event_handlers import OnProcessStart
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def inspection_launch_arguments(role='c2'):
    return [
        DeclareLaunchArgument('inspect_performance', default_value='false', choices=['true', 'false']),
        DeclareLaunchArgument('inspect_interval', default_value='1.0', description='Sample interval, 0.2–60 seconds'),
        DeclareLaunchArgument('inspect_log_directory', default_value='~/weeny_ws/performance_logs'),
        DeclareLaunchArgument('inspection_dashboard', default_value='false', choices=['true', 'false'],
                              description='Open the desktop performance viewer on this computer'),
    ]


def inspection_handler(target_action, role='c2'):
    def started(event, context):
        value = lambda name: LaunchConfiguration(name).perform(context)
        if value('inspect_performance').lower() != 'true':
            return []
        actions = [Node(package='mini_rover_inspection', executable='performance_monitor',
                        name=f'{role}_performance_monitor', output='screen',
                        arguments=['--role', role, '--pid', str(event.pid),
                                   '--namespace', value('namespace'),
                                   '--interval', value('inspect_interval'),
                                   '--log-directory', value('inspect_log_directory')])]
        if value('inspection_dashboard').lower() == 'true':
            actions.append(Node(package='mini_rover_inspection', executable='performance_dashboard',
                                name=f'{role}_performance_dashboard', output='screen',
                                arguments=['--namespace', value('namespace')]))
        return actions
    return RegisterEventHandler(OnProcessStart(target_action=target_action, on_start=started))
