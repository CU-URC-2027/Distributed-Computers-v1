"""Local-only simulator + OC; launch the manual GUI separately or with gui:=true."""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, SetEnvironmentVariable
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare, ExecutableInPackage
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('gui', default_value='true'),
        DeclareLaunchArgument('namespace', default_value='rover1'),
        DeclareLaunchArgument('port', default_value='/tmp/mini-rover-arduino'),
        DeclareLaunchArgument('fault_file', default_value='/tmp/mini-rover-faults.json'),
        SetEnvironmentVariable('ROS_AUTOMATIC_DISCOVERY_RANGE', 'LOCALHOST'),
        ExecuteProcess(cmd=[ExecutableInPackage(package='mini_rover_sim', executable='arduino_sim'),
                            '--port-link', LaunchConfiguration('port'),
                            '--fault-file', LaunchConfiguration('fault_file')], output='screen'),
        Node(package='mini_rover_oc', executable='oc', namespace=LaunchConfiguration('namespace'),
             parameters=[PathJoinSubstitution([FindPackageShare('mini_rover_oc'), 'config', 'simulation.yaml']),
                         {'port': ParameterValue(LaunchConfiguration('port'), value_type=str)}], output='screen'),
        ExecuteProcess(cmd=[ExecutableInPackage(package='mini_rover_c2', executable='c2_gui'), '--namespace', LaunchConfiguration('namespace')],
                       condition=IfCondition(LaunchConfiguration('gui')), output='screen'),
    ])
