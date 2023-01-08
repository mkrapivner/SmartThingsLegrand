/**
 *  Legrand Connect (Unofficial)
 *
 *  Copyright 2019-2023 Matt Krapivner
 *
 *  Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except
 *  in compliance with the License. You may obtain a copy of the License at:
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 *  Unless required by applicable law or agreed to in writing, software distributed under the License is distributed
 *  on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License
 *  for the specific language governing permissions and limitations under the License.
 *
 */
definition(
        name: "Legrand (Connect)",
        namespace: "mkrapivner",
        author: "Matt Krapivner",
        description: "The app to connect to the Node.JS server used for exchanging messages with the Legrand RFLC hub.",
        category: "My Apps",
        iconUrl: "https://s3.amazonaws.com/smartapp-icons/Convenience/Cat-Convenience.png",
        iconX2Url: "https://s3.amazonaws.com/smartapp-icons/Convenience/Cat-Convenience@2x.png",
        iconX3Url: "https://s3.amazonaws.com/smartapp-icons/Convenience/Cat-Convenience@2x.png",
        singleInstance: true
)


preferences {
    page(name:"hubInfo", title:"Legrand Hub Info", content:"hubInfo", install:false, uninstall:true)
    page(name:"hubDiscovery", title:"Connect with your Legrand Hub", content:"hubDiscovery", install: false, uninstall:true)
    page(name:"lightsDiscovery", title:"Add These Lights", content:"lightsDiscovery", refreshInterval:5, install:true)
}

mappings {
    path("/HubNotify") {
        action: [
                POST: "postHubNotify",
                GET: "getHubNotify"
        ]
    }
}

def hubInfo() {
    if (!state.hubConnected && !state.lightsList)
        hubDiscovery()
    else {
        return dynamicPage(name:"hubInfo", title:"Legrand Hub Info", nextPage:"hubDiscovery", uninstall:true, install:false) {
            section{
                paragraph title: "Connected Hub Info",
                        "Hub Model: " + state.hubModel + "\nFirmware Version: " + state.hubFirmwareVersion + "\nFirmware Date: " + state.hubFirmwareDate + "\nFirmware Branch: " + state.hubFirmwareBranch
                paragraph title: "Update State", new groovy.json.JsonBuilder(state.hubUpdateState).toPrettyString()
                paragraph title: "Debug Data", "Hub IP: " + state.legrand_ip + "\nHub Mac Address: " + state.hubMacAddress + "\nHub House ID: " + state.hubHouseID
            }
        }
    }
}

def hubDiscovery() {
    // clear the refresh count for the next page
    state.refreshCount = 0
    state.error = ""

    return dynamicPage(name:"hubDiscovery", title:"Connect with your Legrand Hub", nextPage: "lightsDiscovery", uninstall:true, install:false) {
        section ("Address of the Node.JS server:") {
            input "node_ip", "text", title: "IP address"
            input "node_port", "number", title: "Port"
        }

        section ("Address of the Legrand Hub:") {
            input "legrand_ip", "text", title: "IP address"
        }

        section ("Notify this device:") {
            input "notificationDevice", "capability.notification", multiple: false, required: false
        }
    }
}

def lightsDiscovery() {
    def doRescan = false
    if (state.node_ip != node_ip || state.legrand_ip != legrand_ip) {
        doRescan = true
        state.lightsList = [:]
    }

    state.node_ip = node_ip
    state.node_port = node_port
    state.legrand_ip = legrand_ip
    state.legrand_port = 2112

    if (!state.accessToken) {
        createAccessToken()
    }

    def apiServerUrl = getFullLocalApiServerUrl() + "/HubNotify?access_token=${state.accessToken}"
    // check for timeout error
    state.refreshCount = state.refreshCount+1
    if (state.refreshCount > 20) {
        state.error = "Network Timeout. Check your IP address and port. You must access a local IP address and a non-https port."
    }

    if (!state.initSent || doRescan) {
        state.initSent = true
        state.hubConnected = false
        prepareNodeMessage("/init", ["hubIP":state.legrand_ip, "apiServerUrl":apiServerUrl])
    }

    def options = lightsDiscovered() ?: []
    def numFound = options.size() ?: 0

    log.debug "In lightsDiscovery, found ${numFound} lights"
    if (state.error == "")
    {
        if (!options) {
            // we're waiting for the list to be created
            return dynamicPage(name:"lightsDiscovery", title:"Connecting", nextPage:"", refreshInterval:4, uninstall: true) {
                section("Connecting to ${state.node_ip}:${state.node_port}") {
                    paragraph "This can take a minute. Please wait..."
                }
            }
        } else {
            // we have the list now
            return dynamicPage(name:"lightsDiscovery", title:"Add These Lights", install: true) {
                section("See the available lights:") {
                    input "selectedLights", "enum", required:false, title:"Select Lights (${numFound} found)", multiple:true, options:options
                }
            }
        }
    }
    else
    {
        def error = state.error

        // clear the error
        state.error = ""

        // show the message
        return dynamicPage(name:"lightsDiscovery", title:"Connection Error", nextPage:"", uninstall: true) {
            section() {
                paragraph error
            }
        }
    }
}

def prepareNodeMessage(String nodePath, Map params=null) {
    try {
        def httpMethod = "GET"
        if (nodePath in ["/command", "/init"])
            httpMethod = "POST"

        def reqParams = [uri: "http://${state.node_ip}:${state.node_port}/${nodePath}"]
        if (httpMethod == "POST") {
            reqParams << [body: new groovy.json.JsonBuilder(params).toPrettyString()]
            reqParams << [contentType: "application/json"]
            asynchttpPost("postNotifyCallback", reqParams)
        } else {
            asynchttpGet("getNotifyCallback", reqParams)
        }

        log.debug "Sending ${httpMethod} ${nodePath} command, reqParams: ${reqParams}"
    } catch (e) {
        log.error "Caught exception sending ${httpMethod} status command: ${e}"
    }
}

def sendLegrandHubMessage(Map cmd) {
    prepareNodeMessage("/command", cmd)
}

def getHubNotify() {
    log.trace("In getHubNotify")
}

def postHubNotify() {
    // JSON can be sent either in request.JSON or in params.
    // Handle both (Node.JS sends in request.JSON, python flask sends in params.

    // log.info("In postHubNotify, request: ${request}, params: ${params}")
    def reqJSON = request.JSON?:null
    if (!reqJSON)
        reqJSON = params

    switch (reqJSON?.Service) {
        case "WebServerUpdate":
            if (reqJSON.containsKey("hubConnected")) {
                def hubConnected = reqJSON.hubConnected
                if (state.hubConnected && !hubConnected) {
                    state.hubConnected = false
                    def error = reqJSON.error?:"Unknown"
                    notificationDevice.deviceNotification("Legrand hub disconnected. Error: ${error}") // hail Mary is sent here
                } else if (!state.hubConnected && hubConnected) {
                    state.hubConnected = true
                    // get the list of lights
                    sendLegrandHubMessage(getSystemInfoCmd())
                    sendLegrandHubMessage(getReportSystemPropertiesCmd())
                    sendLegrandHubMessage(getZonesCmd())
                    notificationDevice.deviceNotification("Legrand hub connected")
                }
            }
            break
        case "ZonePropertiesChanged":
            if (!reqJSON.containsKey("ZID"))
                log.error ("ZID not found in POST request. Request JSON: ${reqJSON}")
            else {
                def d = getChildDevice(createDNI(reqJSON.ZID))
                d.propertiesChanged(reqJSON.PropertyList?:null)
            }
            break
        case "ListZones":
            if (!state.lightsList || state.lightsList.size() == 0)
                state.lightsList = [:]
            reqJSON.ZoneList.each { zone ->
                state.lightsList[zone.ZID.toString()] = [:]
                sendLegrandHubMessage(getZonePropertiesCmd(zone.ZID))
            }
            break
        case "ReportZoneProperties":
            def zone = reqJSON.ZID
            def d = getChildDevice(createDNI(zone))
            if (d)
            // device already added, just update it's properties
                d.propertiesChanged(reqJSON.PropertyList?:null)
            state.lightsList[Integer.toString(zone)] = reqJSON.PropertyList
            break
        case "ZoneAdded":
            // TODO
            break
        case "ZoneDeleted":
            // TODO
            break
        case "SystemInfo":
            state.hubModel = reqJSON.Model
            state.hubFirmwareVersion = reqJSON.FirmwareVersion
            state.hubFirmwareDate = reqJSON.FirmwareDate
            state.hubFirmwareBranch = reqJSON.FirmwareBranch
            state.hubMacAddress = reqJSON.MACAddress
            state.hubHouseID = reqJSON.HouseID
            state.hubUpdateState = reqJSON.UpdateState
            break
        default:
            break
    }
    return [foo: reqJSON?.foo, bar: reqJSON?.bar]
}

def postNotifyCallback(response, data) {
    // log.debug ("In postNotifyCallback")
    if (response.hasError()) {
        def errorData = response.getErrorData()
        def errorMsg = response.getErrorMessage()
        // log.debug("In postNotifyCallback, errorData: ${errorData}, errorMsg: ${errorMsg}")
        return
    }
    def status = response.getStatus()
    def respData = response.getData()
    def reqJSON = response.getJson()

    // log.trace ("In postNotifyCallback, status: ${status}, respData: ${respData}, data: ${data}, reqJSON: ${reqJSON}")

    if (reqJSON.containsKey("initReceived")) {
        prepareNodeMessage("/status")
    }
}

def getNotifyCallback(response, data) {
    // log.debug ("In getNotifyCallback")
    if (response.hasError()) {
        def errorData = response.getErrorData()
        def errorMsg = response.getErrorMessage()
        log.trace("In getNotifyCallback, errorData: ${errorData}, errorMsg: ${errorMsg}")
        return
    }
    def status = response.getStatus()
    def respData = response.getData()
    def respJSON = response.getJson()

    log.trace ("In getNotifyCallback, status: ${status}, respData: ${respData}, data: ${data}, respJSON: ${respJSON}")

    if (respJSON.containsKey("initHubConnected")) {
        // This is a response to "/status"
        state.hubConnected = respJSON.initHubConnected
        // get the list of lights, we are initializing
        if (state.hubConnected) {
            sendLegrandHubMessage(getSystemInfoCmd())
            sendLegrandHubMessage(getReportSystemPropertiesCmd())
            sendLegrandHubMessage(getZonesCmd())
        }
    }
}

def lightsDiscovered() {
    log.trace ("In lightsDiscovered")
    def map = [:]
    def allFound = true
    state.lightsList.each { key, val ->
        map[key] = val.Name?: null
        if (!map[key]) {
            allFound = false
            log.warn "Not found zone ${key}"
            sendLegrandHubMessage(getZonePropertiesCmd(key))
        }
        else
            log.trace "found zone ${key}: ${val.Name}"
    }
    if (allFound)
        return map
    else
        return [:]
}

// Definitions of Legrand commands
def getSystemInfoCmd() {
    def cmd = [:]
    cmd.Service = "SystemInfo"
    return cmd
}

def getReportSystemPropertiesCmd() {
    def cmd = [:]
    cmd.Service = "ReportSystemProperties"
    return cmd
}

def getZonesCmd() {
    def cmd = [:]
    cmd.Service = "ListZones"
    return cmd
}

def getZonePropertiesCmd(zone) {
    def zid = zone as Integer
    def cmd = [:]
    cmd.Service = "ReportZoneProperties"
    cmd.ZID = zid
    return cmd
}

def setZonePropertiesCmd(zone, props) {
    def zid = zone as Integer
    def cmd = [:]
    cmd.Service = "SetZoneProperties"
    cmd.ZID = zid
    cmd.PropertyList = props
    return cmd
}

def installed() {
    //log.debug "Installed with settings: ${settings}"
    initialize()
}

def updated() {
    //log.debug "Updated with settings: ${settings}"
    initialize()
}

def initialize() {
    if (selectedLights)
        addLights()
}

def addLights() {
    selectedLights.each { zid ->
        def newLight = state.lightsList[zid.toString()]
        //log.trace "newLight = " + newLight
        if (newLight != null) {
            def newLightDNI = createDNI (zid)
            // log.trace "newLightDNI = " + newLightDNI
            def d = getChildDevice(newLightDNI)
            if(!d) {
                d = addChildDevice("mkrapivner", "Dimmer Switch", newLightDNI, [label: newLight.Name])
                log.trace "created ${d.displayName} with id ${newLightDNI}"

                // set up device capabilities here ??? TODO ???
            } else {
                log.debug "Found existing light ${d.displayName} with DNI ${newLightDNI}, not adding another."
            }
        }
    }
}

def createDNI(zid) {
    return "Legrand " + zid.toString()
}

/////////CHILD DEVICE METHODS

def getLightZIDbyChild(childDevice) {
    return getLightZIDbyName(childDevice.device?.deviceNetworkId)
}

def getLightZIDbyName(String name) {
    if (name) {
        def foundLight = state.lightsList.find { createDNI(it.key).toString() == name.toString()}?.key
        return foundLight
    } else {
        return null
    }
}