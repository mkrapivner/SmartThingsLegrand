# Legrand Adorne for Hubitat
This is an unofficial App and Device Handler for the Legrand Adorne (LC7001 hub). This requires "advanced" setup skills.

*The bridge server is a Python port of the original Node.js version. Any suggestions and improvements are welcome.*

## Requirements
- An "always-on" Linux machine (NAS, Raspberry Pi, etc.) to run as a web server "man in the middle"
- The web server, Legrand hub, and the Hubitat hub need to be on the same local network.

## Setup
### Run the server in Docker (easiest)
- Build the docker image:
```commandline
% cd server
% docker build . -t legrand_hubitat_bridge
```
- Run the docker image using your environment's docker engine (you'll need to expose port 21120). For example,
```
% docker run -p 21120:21120 legrand_hubitat_bridge
```

You should see something like `Server listening on port 21120` on the console

The Hubitat app sends its settings to the bridge, which saves them to `config.json` so they survive a restart. Inside the container that file lives at `/usr/src/app/config.json`. To keep it across container rebuilds, mount a volume:
```
% docker run -p 21120:21120 -v legrand_config:/usr/src/app legrand_hubitat_bridge
```
- Docker will not restart the bridge on its own if the machine reboots. Add `--restart unless-stopped` if you want it to come back up automatically.

### Run the server natively (option 2)
- Install [Python 3](https://www.python.org/downloads/) on the web server machine, if it isn't there already. The installation steps will vary based on your flavor of Linux. (The Docker image uses Python 3.13.)
- Assuming you checked out the source code to `~/legrand`, install the dependencies:
```commandline
% cd ~/legrand/server
% python3 -m venv .venv
% ./.venv/bin/pip install -r requirements.txt
```
- Start the server:
```commandline
% ./.venv/bin/python legrand.py
```
You should see something like `Server listening on port 21120` on the console
- The server writes `config.json` into the current working directory. Set the `LEGRAND_CONFIG` environment variable if you want it somewhere else:
```commandline
% LEGRAND_CONFIG=/etc/legrand/config.json ./.venv/bin/python legrand.py
```
- To keep it running across reboots, run it under whatever supervisor you prefer (`systemd` is the usual choice on Linux).

### Hubitat
- Add code from **LegrandConnect.groovy** as the app, and **LegrandSwitch.groovy** as the device handler.
- Install the app: Apps -> Add User App -> Legrand (Connect).
- Once the app starts, you will need to enter the IP address of the web server, port number of the web server (enter **21120**, unless this port number conflicts with something in your system, in which case change `SERVER_PORT` in `server/legrand.py` and here).
- You will also need to enter the IP address of the Legrand Hub.
- If you want to receive push notifications when the Legrand hub disconnects and reconnects from the bridge server, select your device in "Notify this device" section. The Legrand hub disconnect/reconnect events are the only notifications the app sends.
- After you tap **Next**, it will take a few seconds to discover the Legrand lights in your system.
- Select the ones you want to control from Hubitat, and tap **Done**
- The lights should show up as "Dimmer Switch" under Devices.

## Testing the server
`server/test_bridge.py` runs the bridge against a fake LC7001 hub and a fake Hubitat, so you can check the server end to end without touching real hardware:
```commandline
% cd server
% ./.venv/bin/python test_bridge.py
```
It prints a PASS/FAIL line per check and exits non-zero on failure.

### Notes
- Currently, there is no differentiation between "Switch" and "Dimmer". As a result, the dimmer slider will show up in the device handler even for the switches without the dimmer. It won't hurt anything, but obviously don't expect it to dim your lights as you play around with it... The on/off functionality still works.
