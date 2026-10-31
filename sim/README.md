# The simulation

The station is tested against ArduPlane SITL and Gazebo Harmonic: a Mini Talon V-Tail with
a nose camera (640x480 at 20 Hz, a 64.7° field of view) that sends H.264 in MPEG-TS to UDP
5600, on a runway near Erzurum where a QR target board lies. `start_sim.sh` starts it all,
after stopping a simulation still running:

```bash
sim/start_sim.sh                              # then, each in a terminal of its own:
./start.sh                                    # the station, on tcp:127.0.0.1:5762
.venv/bin/python -E tools/fake_gozcu.py       # Gözcü's MCU, on tcp:127.0.0.1:5763
```

## What it needs, once

On Ubuntu 24.04:

1. **ArduPilot**, in `~/ardupilot`: see ArduPilot's
   [build setup for Linux](https://ardupilot.org/dev/docs/building-setup-linux.html), which
   also installs MAVProxy. `sim_vehicle.py` builds ArduPlane the first time it runs.
2. **Gazebo Harmonic** and **ardupilot_gazebo**, built in `~/gz_ws/src/ardupilot_gazebo`:
   see [its README](https://github.com/ArduPilot/ardupilot_gazebo). Its camera plugin,
   which streams the picture, needs GStreamer:

   ```bash
   sudo apt install libopencv-dev libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
     gstreamer1.0-plugins-bad gstreamer1.0-libav gstreamer1.0-gl
   ```

3. **SITL_Models** in `~/gz_ws/src/SITL_Models`, with this project's changes:

   ```bash
   git clone https://github.com/ArduPilot/SITL_Models.git ~/gz_ws/src/SITL_Models
   git -C ~/gz_ws/src/SITL_Models apply "$PWD/sim/sitl_models.patch"   # from this folder's parent
   ```

   They give the Mini Talon its nose camera, and the runway world the sensors system that
   cameras need, its location near Erzurum and the QR target, whose model is in
   `sim/models`.

Elsewhere than in these folders? Set `ARDUPILOT`, `ARDUPILOT_GAZEBO` and `SITL_MODELS` for
`start_sim.sh`; `ROS_SETUP` is sourced when it exists, for a Gazebo that came with ROS 2
Jazzy, and `MAVPROXY_VENV` activated when MAVProxy lives in a venv. `HOME_LOCATION` moves
the vehicle's home; the world's own location stays Erzurum's.
