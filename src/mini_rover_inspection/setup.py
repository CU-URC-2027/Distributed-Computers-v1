from setuptools import find_packages, setup

setup(name='mini_rover_inspection', version='0.1.0', packages=find_packages(exclude=['test']),
      data_files=[('share/ament_index/resource_index/packages', ['resource/mini_rover_inspection']),
                  ('share/mini_rover_inspection', ['package.xml'])],
      install_requires=['setuptools'], zip_safe=False,
      maintainer='Mini Rover maintainers', maintainer_email='maintainers@example.com',
      description='Optional process and ROS performance inspection', license='Apache-2.0',
      entry_points={'console_scripts': [
          'performance_monitor = mini_rover_inspection.monitor:main',
          'performance_dashboard = mini_rover_inspection.dashboard:main']})
