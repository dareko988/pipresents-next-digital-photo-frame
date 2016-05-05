import time
import datetime
import copy
from Tkinter import *
import Tkinter as tk
import os
import ConfigParser
import subprocess
from pp_utils import Monitor
from pp_options import command_options

class PPIO:
    """
    PPIO provides some IO facilties for Pi presents
     - configures GPIO pins from data in gpio.cfg
     - reads and debounces inputs pins, provides callbacks on state changes which are used to trigger mediashows
     - for output pins allows players to put events, which request the change of state of pins, into a queue. Events are executed at the required time.
    """
 
 
# constants for buttons

# cofiguration from gpio.cfg
    PIN=0                # pin on RPi board GPIO connector e.g. P1-11
    DIRECTION = 1 # IN/OUT/NONE (None is not used)
    NAME = 2      # name for output
    RISING_NAME=3             # name for rising edge callback
    FALLING_NAME=4      # name ofr falling edge callback
    ONE_NAME=5     # name for one state callback
    ZERO_NAME = 6   # name for zero state callback
    REPEAT =  7   #reperat interval for state callbacks (mS)
    THRESHOLD = 8       # threshold of debounce count for state change to be considered
    PULL = 9                  # pull up or down or none
# dynamic data
    COUNT=10          # variable - count of the number of times the input has been 0 (limited to threshold)
    PRESSED = 11      # variable - debounced state 
    LAST = 12       # varible - last state - used to detect edge
    REPEAT_COUNT = 13

    
    TEMPLATE = ['',   #pin
                            '',    # direction
                            '',   #name
                            '','','','',  #input names
                            0,  # repeat
                            0, #threshold
                            '', #pull
                            0,False,False,0]   #dynamics
    
    PINLIST = ('P1-03','P1-05','P1-07','P1-08',
             'P1-10','P1-11','P1-12','P1-13','P1-15','P1-16','P1-18','P1-19',
             'P1-21','P1-22','P1-23','P1-24','P1-26')

    # index of shutdown pin
    SHUTDOWN_INDEX=0
             
# constants for sequencer           
    
    SEQUENCER_PIN = 0         # GPIO pin number, the xx in P1-xx
    SEQUENCER_TO_STATE = 1    # False = off , True =on
    SEQUENCER_TIME = 2        # time since the epoch in seconds
    SEQUENCER_TAG = 3   # tag used to delete all matching event, usually a track reference.

# CLASS VARIABLES
    events=[]
    pins=[]
    last_poll_time=0
    options=None
    # gpio_enabled=False

    #NIK
    SHUTOFF_DELAY = 120
    # motion sensor timeout, i.e. after SHUTOFF_DELAY seconds of no motion
    # pipresents pauses and the monitor turns off

    
    EVENT_TEMPLATE=[0,False,0,None]

    #executed by main program and by each object using gpio
    def __init__(self):
        self.mon=Monitor()
        self.mon.on()
        self.options=command_options()

     # executed once from main program   
    def init(self,pp_dir,pp_home,pp_profile,widget,button_tick,callback=None):
        
        # instantiate arguments
        self.widget=widget
        self.pp_dir=pp_dir
        self.pp_profile=pp_profile
        self.pp_home=pp_home
        self.button_tick=button_tick
        self.callback=callback

        # NIK
        self.turned_off = False
        self.last_motion_time = time.time()
        self.paused = False
        # initializing variables

        PPIO.SHUTDOWN_INDEX=0

        # read gpio.cfg file.
        if self.read(self.pp_dir,self.pp_home,self.pp_profile)==False:
            return False

        import RPi.GPIO as GPIO
        self.GPIO = GPIO
        
        #construct the GPIO control list from the configuration
        for index, pin_def in enumerate(PPIO.PINLIST):
            pin=copy.deepcopy(PPIO.TEMPLATE)
            pin_bits = pin_def.split('-')
            pin_num=pin_bits[1:]
            pin[PPIO.PIN]=int(pin_num[0])
            if self.config.has_section(pin_def)==False:
                self.mon.log(self, "no pin definition for "+ pin_def)
                pin[PPIO.DIRECTION]='None'            
            else:
                # unused pin
                if self.config.get(pin_def,'direction')=='none':
                    pin[PPIO.DIRECTION]='none'
                else:
                    pin[PPIO.DIRECTION]=self.config.get(pin_def,'direction')
                    if pin[PPIO.DIRECTION]=='in':
                        # input pin
                        pin[PPIO.RISING_NAME]=self.config.get(pin_def,'rising-name')
                        pin[PPIO.FALLING_NAME]=self.config.get(pin_def,'falling-name')
                        pin[PPIO.ONE_NAME]=self.config.get(pin_def,'one-name')
                        pin[PPIO.ZERO_NAME]=self.config.get(pin_def,'zero-name')
                        if pin[PPIO.FALLING_NAME]=='pp-shutdown':
                            PPIO.SHUTDOWN_INDEX=index
                        if self.config.get(pin_def,'repeat')<>'':
                            pin[PPIO.REPEAT]=int(self.config.get(pin_def,'repeat'))
                        else:
                            pin[PPIO.REPEAT]=-1
                        pin[PPIO.THRESHOLD]=int(self.config.get(pin_def,'threshold'))
                        if self.config.get(pin_def,'pull-up-down')=='up':
                            pin[PPIO.PULL]=GPIO.PUD_UP
                        elif self.config.get(pin_def,'pull-up-down')=='down':
                            pin[PPIO.PULL]=GPIO.PUD_DOWN
                        else:
                            pin[PPIO.PULL]=GPIO.PUD_OFF
                    else:
                        # output pin
                        pin[PPIO.NAME]=self.config.get(pin_def,'name')
 
            # print pin            
            PPIO.pins.append(copy.deepcopy(pin))

        # setup GPIO
        self.GPIO.setwarnings(False)        
        self.GPIO.setmode(self.GPIO.BOARD)
        

        # set up the GPIO inputs and outputs
        for index, pin in enumerate(PPIO.pins):
            num = pin[PPIO.PIN]
            if pin[PPIO.DIRECTION]=='in':
                self.GPIO.setup(num,self.GPIO.IN,pull_up_down=pin[PPIO.PULL])

                #NIK
                if pin[PPIO.RISING_NAME] == 'pir-switch-on':
                    if self.GPIO.input(num):
                        self.pir_switch_on = True
                    else:
                        self.pir_switch_on = False
                #NIK

            elif pin[PPIO.DIRECTION]=='out':
                self.GPIO.setup(num,self.GPIO.OUT)
                self.GPIO.setup(num,False)
        self.reset_inputs()
        PPIO.gpio_enabled=True

        #init timer
        self.button_tick_timer=None
        PPIO.last_scheduler_time=long(time.time())
        return True

    # called by main program only         
    def poll(self):
        # look at the buttons
        self.do_buttons()

        # kick off output pin sequencer
        poll_time=long(time.time())
        # is current time greater than last time the sceduler was run (previous second or more)
        # run in a loop to catch up because root.after can get behind when images are being rendered etc.
        while PPIO.last_scheduler_time<=poll_time:
            self.do_sequencer(PPIO.last_scheduler_time)
            PPIO.last_scheduler_time +=1
        
        # and loop
        self.button_tick_timer=self.widget.after(self.button_tick,self.poll)


# called by main program only                
    def terminate(self):
        if self.button_tick_timer<>None:
            self.widget.after_cancel(self.button_tick_timer)
        self.clear_events_list(None)
        self.reset_outputs()
        self.GPIO.cleanup()


# ************************************************
# gpio input functions
# called by main program only
# ************************************************
    
    def reset_inputs(self):
        for pin in PPIO.pins:
            pin[PPIO.COUNT]=0
            pin[PPIO.PRESSED]==False
            pin[PPIO.LAST]==False
            pin[PPIO.REPEAT_COUNT]=pin[PPIO.REPEAT]

    # index is of the pins array, provided by the callback ***** needs to be name
    def shutdown_pressed(self):
        if PPIO.SHUTDOWN_INDEX<>0:
            return PPIO.pins[PPIO.SHUTDOWN_INDEX][PPIO.PRESSED]
        else:
            return False

    #NIK
    # turn on and off monitor
    def turn_on(self):
        subprocess.call("vcgencmd display_power 1", shell=True)

    def turn_off(self):
        subprocess.call("vcgencmd display_power 0", shell=True)
    #NIK

    def do_buttons(self):
        # NIK
        # If motion hasn't been detectedin SHUTOFF_DELAY seconds, turn off monitor and pause
        if self.pir_switch_on and not self.turned_off and not self.paused and time.time() > (self.last_motion_time + PPIO.SHUTOFF_DELAY):
            # self.mon.log(self,"PIR not motion. self.pir_switch_on: "+str(self.pir_switch_on)+", self.turned_off: "+str(self.turned_off)+", self.paused: "+str(self.paused))
            # os.system ("echo `date` PIR not motion. self.pir_switch_on: "+str(self.pir_switch_on)+", self.turned_off: "+str(self.turned_off)+", self.paused: "+str(self.paused)+" >> /home/pi/pir.log")
            self.turned_off = True
            # turn off monitor and pause pi presents
            self.turn_off()
            self.callback(1,"pp-pause","rising")
        #NIK

        for index, pin in enumerate(PPIO.pins):
            if pin[PPIO.DIRECTION]=='in':
                # debounce
                if self.GPIO.input(pin[PPIO.PIN])==0:
                    if pin[PPIO.COUNT]<pin[PPIO.THRESHOLD]:
                        pin[PPIO.COUNT]+=1
                        if pin[PPIO.COUNT]==pin[PPIO.THRESHOLD]:
                            # if pin[PPIO.RISING_NAME]=='PIR':
                                # os.system ("echo `date` debounce 1. PPIO.COUNT: "+str(pin[PPIO.COUNT])+" >> /home/pi/pir.log")
                            pin[PPIO.PRESSED]=True
                else: # input is 1
                    if pin[PPIO.COUNT]>0:
                        pin[PPIO.COUNT]-=1
                        if pin[PPIO.COUNT]==0:
                            # if pin[PPIO.RISING_NAME]=='PIR':
                                # os.system ("echo `date` debounce 2. PPIO.COUNT: "+str(pin[PPIO.COUNT])+" >> /home/pi/pir.log")
                            pin[PPIO.PRESSED]=False
     
                #detect edges
                # falling edge
                if pin[PPIO.PRESSED]==True and pin[PPIO.LAST]==False:
                    pin[PPIO.LAST]=pin[PPIO.PRESSED]
                    pin[PPIO.REPEAT_COUNT]=pin[PPIO.REPEAT]
                    self.mon.log(self,"Falling edge: " + pin[PPIO.FALLING_NAME])

                    # NIK
                    # when motion is detected, unpause pipresents and turn on monitor
                    if pin[PPIO.FALLING_NAME]=='pir-switch-off' and self.callback <> None:
                        if self.pir_switch_on:
                            self.pir_switch_on = False
                            if self.turned_off:
                                self.turned_off = False
                                self.turn_on()
                                self.callback(index,"pp-pause","rising")

                    elif pin[PPIO.FALLING_NAME]=='pp-pause' and self.callback <> None:
                        # if monitor is off, then show is paused by program (not user)
                        # so pressing pause just turns monitor back on
                        if self.turned_off:
                            self.turn_on()
                            self.turned_off = False
                            self.paused = True

                        else:
                            if self.paused:
                                self.paused = False
                                self.last_motion_time = time.time()
                            else:
                                self.paused = True
                            self.callback(index,"pp-pause","rising")

                    elif pin[PPIO.FALLING_NAME] in ('pp-up','pp-down') and self.callback <> None:
                        if self.turned_off:
                            self.turned_off = False
                            self.turn_on()
                            self.callback(index,"pp-pause","rising")
                        self.callback(index, pin[PPIO.FALLING_NAME],"falling")
                    #NIK

                    else:
                        if pin[PPIO.FALLING_NAME]<>'' and self.callback <> None:
                            self.callback(index, pin[PPIO.FALLING_NAME],"falling")

               #rising edge
                if pin[PPIO.PRESSED]==False and pin[PPIO.LAST]==True:
                    pin[PPIO.LAST]=pin[PPIO.PRESSED]
                    pin[PPIO.REPEAT_COUNT]=pin[PPIO.REPEAT]
                    self.mon.log(self,"Rising edge: " + pin[PPIO.RISING_NAME])

                    # NIK
                    # If motion is detected and pipresents is not paused, then simply update the last motion time
                    # If motion is detected and pipresents is paused, then unpause and turn on monitor
                    if pin[PPIO.RISING_NAME]=='PIR' and self.callback <> None:
                        if self.pir_switch_on:
                            # os.system ("echo `date` PIR motion 1. self.pir_switch_on: "+str(self.pir_switch_on)+", self.turned_off: "+str(self.turned_off)+", self.paused: "+str(self.paused)+", repeat count: "+str(pin[PPIO.REPEAT_COUNT])+" >> /home/pi/pir.log")
                            self.last_motion_time = time.time()
                            if self.turned_off:
                                # self.mon.log(self,"PIR motion. self.pir_switch_on: "+str(self.pir_switch_on)+", self.turned_off: "+str(self.turned_off)+", self.paused: "+str(self.paused))
                                # os.system ("echo `date` PIR motion 2. self.pir_switch_on: "+str(self.pir_switch_on)+", self.turned_off: "+str(self.turned_off)+", self.paused: "+str(self.paused)+", repeat count: "+str(pin[PPIO.REPEAT_COUNT])+" >> /home/pi/pir.log")
                                self.turned_off = False
                                # turn on monitor and unpause pi presents
                                self.turn_on()
                                self.callback(index,"pp-pause","rising")

                    elif pin[PPIO.RISING_NAME]=='pir-switch-on' and self.callback <> None:
                        if not self.pir_switch_on:
                            self.last_motion_time = time.time()
                            self.pir_switch_on = True
                    else:
                        if pin[PPIO.RISING_NAME]<>'' and self.callback <> None:
                            self.callback(index, pin[PPIO.RISING_NAME],"rising")

                # do state callbacks
                if pin[PPIO.REPEAT_COUNT]==0:
                    if pin[PPIO.ZERO_NAME]<>'' and pin[PPIO.PRESSED]==True and self.callback<>None:
                        self.callback(index, pin[PPIO.ZERO_NAME],"zero")

                    if pin[PPIO.ONE_NAME]<>'' and pin[PPIO.PRESSED]==False and self.callback<>None:
                        self.callback(index, pin[PPIO.ONE_NAME],"zero")

                    pin[PPIO.REPEAT_COUNT]=pin[PPIO.REPEAT]
                else:
                    if pin[PPIO.REPEAT]<>-1:
                        pin[PPIO.REPEAT_COUNT]-=1

                    
# ************************************************
# gpio output sequencer functions
# ************************************************

    # execute events at the appropriate time and remove from list (runs from main program only)
    # runs through list a number of times because of problems with pop messing up list
    def do_sequencer(self,schedule_time):
        # print 'sequencer run for: ' + str(schedule_time) + ' at ' + str(long(time.time()))
        while True:
            event_found=False
            for index, item in enumerate(PPIO.events):
                if item[PPIO.SEQUENCER_TIME]<=schedule_time:
                    event=PPIO.events.pop(index)
                    event_found=True
                    self.do_event(event[PPIO.SEQUENCER_PIN],event[PPIO.SEQUENCER_TO_STATE],item[PPIO.SEQUENCER_TIME])
                    break
            if event_found==False: break

    # execute an event
    def do_event(self,pin,to_state,req_time):
        self.mon.log (self,'pin P1-'+ str(pin)+ ' set  '+ str(to_state) + ' required: ' + str(req_time)+ ' actual: ' + str(long(time.time())))
        # print 'pin P1-'+ str(pin)+ ' set  '+ str(to_state) + ' required: ' + str(req_time)+ ' actual: ' + str(long(time.time()))
        self.GPIO.output(pin,to_state)

# ************************************************
# gpio output sequencer interface methods
# these can be called from many classes so need to operate on class variables
# ************************************************
    def animate(self,text,tag):
        if self.options['gpio']==True:
            lines = text.split("\n")
            for line in lines:
                error_text=self.parse_animate_fields(line,tag)
                if error_text <>'':
                    return 'error',error_text
            return 'normal',''
        return 'normal',''

    # clear event list
    def clear_events_list(self,tag):
        if self.options['gpio']==True:
            self.mon.log(self,'clear events list ')
            # empty event list
            if tag==None:
                PPIO.events=[]
            else:
                self.remove_events(tag)

    def reset_outputs(self):
        if self.options['gpio']==True:
            self.mon.log(self,'reset outputs')
            for index, pin in enumerate(PPIO.pins):
                num = pin[PPIO.PIN]
                if pin[PPIO.DIRECTION]=='out':
                    self.GPIO.output(num,False)

# ************************************************
# internal functions
# these can be called from many classes so need to operate on class variables
# ************************************************

    def parse_animate_fields(self,line,tag):
        fields= line.split()
        if len(fields)==0:
            return ''
            
        name=fields[0]
        pin= self.pin_of(name)
        if pin ==-1:
            return 'Unknown gpio logical output in: ' + line
       
        to_state_text=fields[1]
        if not (to_state_text  in ('on','off')):
            return 'Illegal to-state in : '+ line
        
        if to_state_text == 'on':
            to_state=True
        else:
            to_state=False
            
        if len(fields)==2:
            delay_text='0'
        else:
            delay_text=fields[2]
        
        if  not delay_text.isdigit():
            return 'Delay is not an integer in : '+ line
        delay=int(delay_text)
        
        self.add_event(pin,to_state,delay,tag)
        # self.print_events()
        return ''

    def pin_of(self,name):
        for pin in PPIO.pins:
            # print " in list" + pin[PPIO.NAME] + str(pin[PPIO.PIN] )
            if pin[PPIO.NAME]==name and pin[PPIO.DIRECTION]=='out':
                return pin[PPIO.PIN]
        return -1

    def print_events(self):
        print
        for i in PPIO.events:
            print i

    def add_event(self,sequencer_pin,sequencer_to_state,sequencer_time,sequencer_tag):
        poll_time=long(time.time())
        # delay is 0 so just do it, don't queue it.
        #if sequencer_time == 0:
            #print "firing now",poll_time
            #self.do_event(sequencer_pin,sequencer_to_state,poll_time)
            #return
        # prepare the event
        event=PPIO.EVENT_TEMPLATE
        event[PPIO.SEQUENCER_PIN]=sequencer_pin
        event[PPIO.SEQUENCER_TO_STATE]=sequencer_to_state
        event[PPIO.SEQUENCER_TIME]=sequencer_time+poll_time+1
        event[PPIO.SEQUENCER_TAG]=sequencer_tag
        # print event
        # find the place in the events list and insert
        # first item in the list is earliest, if two have the same time then last to be added is fired last.
        abs_time=sequencer_time+poll_time
        copy_event= copy.deepcopy(event)
        for index, item in enumerate(PPIO.events):
            if abs_time<item[PPIO.SEQUENCER_TIME]:
                PPIO.events.insert(index,copy_event)
                return copy_event
        PPIO.events.append(copy_event)
        return copy_event
    
    # remove an event not used and does not work
    def remove_event(self,event):
        for index, item in enumerate(PPIO.events):
            if event==item:
                del PPIO.events[index]
                return True
        return False


    # remove all the events with the same tag, usually a track reference
    def remove_events(self,tag):
        left=[]
        for item in PPIO.events:
            if tag<>item[PPIO.SEQUENCER_TAG]:
                left.append(item)
        PPIO.events= left
        #self.print_events()



# ***********************************
# reading gpio.cfg functions
# ************************************

    def read(self,pp_dir,pp_home,pp_profile):
            # try inside profile
            tryfile=pp_profile+os.sep+"gpio.cfg"
            # self.mon.log(self,"Trying gpio.cfg in profile at: "+ tryfile)
            if os.path.exists(tryfile):
                 filename=tryfile
            else:
                # try inside pp_home
                # self.mon.log(self,"gpio.cfg not found at "+ tryfile+ " trying pp_home")
                tryfile=pp_home+os.sep+"gpio.cfg"
                if os.path.exists(tryfile):
                    filename=tryfile
                else:
                    # try inside pipresents
                    # self.mon.log(self,"gpio.cfg not found at "+ tryfile + " trying inside pipresents")
                    tryfile=pp_dir+os.sep+'pp_home'+os.sep+"gpio.cfg"
                    if os.path.exists(tryfile):
                        filename=tryfile
                    else:
                        self.mon.log(self,"gpio.cfg not found at "+ tryfile)
                        self.mon.err(self,"gpio.cfg not found")
                        return False   
            self.config = ConfigParser.ConfigParser()
            self.config.read(filename)
            self.mon.log(self,"gpio.cfg read from "+ filename)
            return True



# ******************************
# test harness
# ******************************

if __name__ == '__main__':



    def callback(index,name,edge):
        global pevent
        if name == 'play':
            #print name,  edge
            # event with 0 delay is executed immeadiately and cannot be removed.
            # pin, state, time, tag
            ppio.add_event(0,1,0,1)
            ppio.add_event(2,1,2,1)
            ppio.add_event(3,1,3,1)
            ppio.add_event(4,1,3,2)
            ppio.add_event(1,1,1,1)
            ppio.add_event(5,1,10,2)
            pevent=ppio.add_event(6,1,11,2)
        elif name=='pause':
            ppio.remove_events(2)
            ppio.remove_event(pevent)

        

    pevent=None
    
    pp_dir='/home/pi/pipresents-next'
    pp_profile='/home/pi/pp_home/pp_profiles/trigger_test'
    Monitor.log_path=pp_dir
    Monitor.global_enable=True
    print "runnning"
    my_window=Tk()
    my_window.title("PPIO Test Harness")
    ppio=PPIO()
    ppio.init(pp_dir,pp_profile,my_window,50,callback)
    ppio.read()
    ppio.poll()
    my_window.mainloop()


    





        
