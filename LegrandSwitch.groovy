/**
 *  Legrand (Unofficial) Dimmer Switch Device Handler
 *  Copyright 2019 Matt Krapivner
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
metadata {
    definition (name: "Dimmer Switch", namespace: "mkrapivner", author: "Matt Krapivner") {
        capability "Switch Level"
        capability "Actuator"
        capability "Indicator"
        capability "Switch"
    }

    simulator {
    }

    preferences {
        // input "ledIndicator", "enum", title: "LED Indicator", description: "Turn LED indicator on... ", required: false, options:["on": "When On", "off": "When Off", "never": "Never"], defaultValue: "off"
    }

    tiles(scale: 2) {
        multiAttributeTile(name:"switch", type: "lighting", width: 6, height: 4, canChangeIcon: true) {
            tileAttribute ("device.switch", key: "PRIMARY_CONTROL") {
                attributeState "on", label:'${name}', action:"switch.off", icon:"st.switches.switch.on", backgroundColor:"#00a0dc", nextState:"turningOff"
                attributeState "off", label:'${name}', action:"switch.on", icon:"st.switches.switch.off", backgroundColor:"#ffffff", nextState:"turningOn"
                attributeState "turningOn", label:'Turning On', action:"switch.off", icon:"st.switches.switch.on", backgroundColor:"#00a0dc", nextState:"turningOff"
                attributeState "turningOff", label:'Turning Off', action:"switch.on", icon:"st.switches.switch.off", backgroundColor:"#ffffff", nextState:"turningOn"
            }
            tileAttribute ("device.level", key: "SLIDER_CONTROL") {
                attributeState "level", action:"switch level.setLevel"
            }
        }

        /*
		main(["switch"])
        details(["switch", "levelSliderControl"])
        */
    }
}

def propertiesChanged (propList) {
    if (!propList) {
        log.warn "Empty property list received"
        return
    }

    propList.each { key, val ->
        if (key == "Power") {
            if (val.toBoolean() == true && device.currentState("switch")?.value == "off")
                sendEvent(name: "switch", value: "on")
            else if (val.toBoolean() == false && device.currentState("switch")?.value == "on")
                sendEvent(name: "switch", value: "off")
        } else if (key == "PowerLevel") {
            if (val.toInteger() != device.currentState("level")?.value) {
                sendEvent(name: "level", value: val.toInteger())
            }
        } else
            log.warn ("Unknown property in property list: ${key}. If this is a new device, disregard this message.")
    }
}

def installed() {
    // TODO: What do we put here?
}

def updated(){
    // TODO: What do we put here?
}

def parse(String description) {
    // This must be empty
}

def on() {
    def zid = getLightZID()
    def cmd = parent.setZonePropertiesCmd(zid, ["Power": true])
    parent.sendLegrandHubMessage(cmd)
}

def off() {
    def zid = getLightZID()
    def cmd = parent.setZonePropertiesCmd(zid, ["Power": false])
    parent.sendLegrandHubMessage(cmd)
}

def setLevel(value) {
    def zid = getLightZID()
    def cmd = parent.setZonePropertiesCmd(zid, ["PowerLevel": value])
    parent.sendLegrandHubMessage(cmd)
}

def getLightZID() {
    def lightZid = parent.getLightZIDbyChild(this)
    if (lightZid == null) {
        log.trace "could not find device DNI = ${device.deviceNetworkId}"
    }
    return (lightZid)
}