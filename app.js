// TO GET ST IP AND PORT (port is usually 39500)
// device.hub.getDataValue(“localIP”); device.hub.getDataValue(“localSrvPortTCP”).
// Device handler to handle incoming events: https://community.smartthings.com/t/how-can-i-receive-lan-messages-on-the-st-hub-when-the-messages-can-come-at-any-point/54912/9

const telnet = require('telnet-client');
const express = require('express');
const request = require('request');
const bodyParser = require('body-parser');

const server = new telnet();
const app = express();

const SERVER_PORT = 21120;
const LC7001_PORT = 2112;

var HUB_IP = "";
var API_SERVER_URL = "";

app.use(bodyParser.json());

app.listen(SERVER_PORT, () => {
  console.log(`Server listening on port ${SERVER_PORT}`);
});

app.post('/init', function (req, res) {
  let command = req.body;
  HUB_IP = command.hubIP;
  API_SERVER_URL = command.apiServerUrl;
  console.log(`Hub IP: ${HUB_IP}`);
  console.log(`API Server URL: ${API_SERVER_URL}`);
  // connect to server
  lc7001.connected = false;
  server.connect({
    host: HUB_IP,
    port: LC7001_PORT,
    timeout: 10000
  });
  res.json({
    "initReceived": true
  })
});

app.get('/status', function (req, res) {
  //console.log ("Received /status request");
  res.json({
    "hubConnected": lc7001.connected
  })
});

app.post('/command', function (req, res) {
  let command = req.body;
  command.ID = lc7001.commandID;
  console.log ("Received /command: " + JSON.stringify(command));
  if ("ZID" in command) {
    if (command.ZID == "null")
      command.ZID = 0; // Sometimes parsing 0 messes up
    else
      command.ZID = parseInt(command.ZID);
    // console.log ("Received /command after parsing ZID: " + JSON.stringify(command));
  }
  server.send(JSON.stringify(command) + lc7001.delimiter);
  res.json({ message: 'Command received' });
});

lc7001 = {
  _commandID : 0,
  connected : false,
  delimiter : '\0',
  lastError : "",

  get commandID() {
    if (this._commandID >= Number.MAX_SAFE_INTEGER) {
      this._commandID = 1;
    } else {
      this._commandID = this._commandID + 1;
    }
    return this._commandID;
  }
};

// display server response
server.on("data", function(data){
  // console.log("RAW DATA: " + data);
  let splitData = String(data).split('\0');
  splitData.forEach(function (resp) {
    if (resp.length > 0) {
      let response = JSON.parse(resp);
      if (["ping", "EliotErrors", "BroadcastDiagnostics", "BroadcastMemory", "SystemPropertiesChanged"].indexOf(response.Service) == -1) {
        // Send a POST request to SmartThings
        console.log ("Notifying SmartThings with POST update: " + resp);
        // console.log(response);
        sendToSmartThings(response);
      }
    }
  });
});

// login when connected
server.on("connect", function(){
  lc7001.connected = true;
  console.log("Connected to hub successfully");
  sendToSmartThings({
    "hubConnected": true
  })
  // server.send('{"ID":' + String(lc7001.commandID) + ',"Service":"SystemInfo"}' + '\0');
  // server.send('{"ID":' + String(lc7001.commandID) + ',"Service":"ReportSystemProperties"}' + '\0');
  // server.send('{"ID":' + String(lc7001.commandID) + ',"Service":"ListZones"}' + '\0');
});

server.on("error", function (err) {
  console.log('Socket error: ' + err);
  lc7001.connected = false;
  lc7001.lastError = err;
});

server.on("close", function (err) {
  console.log('Socket closed: ' + err);
  lc7001.connected = false;
  sendToSmartThings({
    "hubConnected": false,
    "error": lc7001.lastError
  });
  lc7001.lastError = "";
  console.log('Re-establishing connection to Legrand hub');
  server.connect({
    host: HUB_IP,
    port: LC7001_PORT,
    timeout: 10000
  });
});

function sendToSmartThings(jsonBody) {
  if (!("Service" in jsonBody))
    jsonBody["Service"] = "WebServerUpdate";
  request.post(API_SERVER_URL, {
    json: jsonBody
  }, (error, res, body) => {
    if (error) {
      console.error(error);
    }
  })
}