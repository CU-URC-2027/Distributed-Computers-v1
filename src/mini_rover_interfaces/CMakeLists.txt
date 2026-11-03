cmake_minimum_required(VERSION 3.8)
project(mini_rover_interfaces)

find_package(ament_cmake REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(rosidl_default_generators REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/MotorTelemetry.msg"
  "msg/Fault.msg"
  "msg/DriveIntent.msg"
  "msg/ControlState.msg"
  "msg/WheelState.msg"
  "msg/ChassisState.msg"
  "srv/Claim.srv"
  "srv/Control.srv"
  DEPENDENCIES builtin_interfaces
)
ament_export_dependencies(rosidl_default_runtime)
ament_package()
