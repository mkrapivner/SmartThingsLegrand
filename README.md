# SmartThingsLegrand
This is an unofficial SmartApp and Device Handler for the Legrand Adorne (LC7001 hub), after Legrand moved out of the Artik cloud. This requires "advanced" setup skills.

*The web server is the author's first node.js project, and as such it is likely not optimal. Any suggestions and improvements are welcome.*
## Requirements
- An "always-on" Linux machine (NAS, Raspberry Pi, etc.) to run as a web server "man in the middle"
- The web server, Legrand hub, and the SmartThings hub need to be on the same local network.

## Setup
- If you don't have node installed yet, install [node](https://nodejs.org/en/download/) on the web server machine. The installation steps will vary based on your flavor of Linux.
- You may need to install some node modules. After node has been installed, run:

`npm install telnet-client`  
`npm install express`  
`npm install request`  
- The JavaScript app can sometimes crash, so it is wise to run it under a monitoring app. I use `pm2`, but you can use whatever you want.
### On the web server
- Assuming you checked out the source code to `~/legrand`:  
`cd ~/legrand`  
`node app.js`  
You should see something like `Server listening on port 21120` on the console
- If you have installed `pm2`, instead of starting `node app.js` directly, you can start the web server as follows:  
`cd ~/legrand`  
`pm2 start app`

### SmartThings
- Open SmartThings IDE, and add code from **LegrandConnect.groovy** as the SmartApp, and **LegrandSwitch.groovy** as the device handler.
- From your mobile SmartThings app, install SmartApp -> My Apps -> Legrand (Connect).
- On the SmartApp screen, you will need to enter the IP address of the web server, port number of the web server (enter **21120**, unless this port number conflicts with something in your system, in which case change it in app.js code and here).
- You will also need to enter the IP address of the Legrand Hub.
- After you tap **Next**, it will take a few seconds to discover the Legrand lights in your system.
- Select the ones you want to control from SmartThings, and tap **Save**
- The lights should show up under "Things" in SmartThings.

### Notes
- Currently, there is no differentiation between "Switch" and "Dimmer". As a result, the dimmer slider will show up in the device handler even for the switches without the dimmer. It won't hurt anything, but obviously don't expect it to dim your lights as you play around with it... The on/off functionality still works.
