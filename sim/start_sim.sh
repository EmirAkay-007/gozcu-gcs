#!/usr/bin/env bash
# The simulation the station is tested against, in one command: Gazebo Harmonic with the
# Mini Talon V-Tail, whose nose camera sends H.264 to UDP 5600, and ArduPlane SITL with
# MAVProxy. SITL's serial ports are then TCP 5760 (MAVProxy), 5762 (the station) and
# 5763 (Gözcü's MCU, or tools/fake_gozcu.py); MAVProxy also passes the link on to UDP
# 14550 and 14551.
#
#   sim/start_sim.sh
#
# sim/README.md says what it needs. The variables below say where it looks for it and
# can be set to look elsewhere. A simulation still running is stopped first.
set +u  # ROS's and the venvs' scripts read variables that may be unset

here=$(dirname "$(readlink -f "$0")")
ARDUPILOT=${ARDUPILOT:-$HOME/ardupilot}
ARDUPILOT_GAZEBO=${ARDUPILOT_GAZEBO:-$HOME/gz_ws/src/ardupilot_gazebo}
SITL_MODELS=${SITL_MODELS:-$HOME/gz_ws/src/SITL_Models}
ROS_SETUP=${ROS_SETUP:-/opt/ros/jazzy/setup.bash}  # where Gazebo comes with ROS 2 Jazzy
MAVPROXY_VENV=${MAVPROXY_VENV:-$HOME/venv-ardupilot}  # where MAVProxy has a venv of its own
HOME_LOCATION=${HOME_LOCATION:-39.9035714,41.2375758,1850,0}  # lat,lon,metres,heading: Erzurum

world=vtail_runway.sdf
params="$SITL_MODELS/Gazebo/config/mini_talon_vtail.param"
camera=/world/runway/model/mini_talon_vtail/link/base_link/sensor/nose_camera/image/enable_streaming

missing() {
  echo "Not found: $1 ($2; see sim/README.md)" >&2
  exit 1
}
[ -x "$ARDUPILOT/Tools/autotest/sim_vehicle.py" ] || missing "$ARDUPILOT" "ArduPilot"
[ -f "$ARDUPILOT_GAZEBO/build/libGstCameraPlugin.so" ] ||
  missing "$ARDUPILOT_GAZEBO/build/libGstCameraPlugin.so" "ardupilot_gazebo, built"
[ -f "$params" ] || missing "$params" "SITL_Models"
grep -q nose_camera "$SITL_MODELS/Gazebo/models/mini_talon_vtail/model.sdf" ||
  missing "the nose camera in $SITL_MODELS" "apply sim/sitl_models.patch"

if [ -f "$ROS_SETUP" ]; then source "$ROS_SETUP"; fi
export GZ_SIM_SYSTEM_PLUGIN_PATH="$ARDUPILOT_GAZEBO/build:${GZ_SIM_SYSTEM_PLUGIN_PATH:-}"
models="$ARDUPILOT_GAZEBO/models:$ARDUPILOT_GAZEBO/worlds"
models="$models:$SITL_MODELS/Gazebo/models:$SITL_MODELS/Gazebo/worlds:$here/models"
export GZ_SIM_RESOURCE_PATH="$models:${GZ_SIM_RESOURCE_PATH:-}"
export PATH="$ARDUPILOT/Tools/autotest:$PATH"
if [ -f "$MAVPROXY_VENV/bin/activate" ]; then source "$MAVPROXY_VENV/bin/activate"; fi
if command -v nvidia-smi >/dev/null; then  # render on the NVIDIA GPU of a laptop with two
  export __NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia
fi

echo ">>> Stopping a simulation still running"
pkill -f "^gz sim"
pkill -x arduplane
pkill -f "mavproxy\.py"
sleep 2

echo ">>> Gazebo ($world)"
gz sim -v4 -r "$world" &

echo ">>> Waiting for the nose camera"
for second in $(seq 1 40); do
  if gz topic -l 2>/dev/null | grep -q nose_camera; then
    echo ">>> Camera ready ($second s)"
    break
  fi
  sleep 1
done
sleep 2
echo ">>> Camera streaming to UDP 5600"
gz topic -t "$camera" -m gz.msgs.Boolean -p "data: true"

echo ">>> ArduPlane SITL and MAVProxy"
cd "$ARDUPILOT/ArduPlane" || exit 1
exec sim_vehicle.py -v ArduPlane -f plane --model JSON --add-param-file="$params" \
  --custom-location="$HOME_LOCATION" --out=udp:127.0.0.1:14551
