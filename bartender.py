import time
import sys
import RPi.GPIO as GPIO
import json
import threading
import traceback

#from dotstar import Adafruit_DotStar
from menu import MenuItem, Menu, Back, MenuContext, MenuDelegate
from drinks import drink_list, drink_options

from subprocess import Popen, PIPE
from time import sleep
from datetime import datetime
import board
import digitalio
import adafruit_character_lcd.character_lcd as characterlcd

# Modify this if you have a different sized character LCD
lcd_columns = 16
lcd_rows = 2

# compatible with all versions of RPI as of Jan. 2019
# v1 - v3B+
lcd_rs = digitalio.DigitalInOut(board.D22)
lcd_en = digitalio.DigitalInOut(board.D17)
lcd_d4 = digitalio.DigitalInOut(board.D25)
lcd_d5 = digitalio.DigitalInOut(board.D24)
lcd_d6 = digitalio.DigitalInOut(board.D23)
lcd_d7 = digitalio.DigitalInOut(board.D18)

GPIO.setmode(GPIO.BCM)

LEFT_BTN_PIN = 13
LEFT_PIN_BOUNCE = 1000

RIGHT_BTN_PIN = 4
RIGHT_PIN_BOUNCE = 2000

NUMBER_NEOPIXELS = 45
NEOPIXEL_DATA_PIN = 26
NEOPIXEL_CLOCK_PIN = 6
NEOPIXEL_BRIGHTNESS = 64

FLOW_RATE = 60.0/100.0

class Bartender(MenuDelegate): 
    def __init__(self):
        self.running = False

        # set the oled screen height

        self.btn1Pin = LEFT_BTN_PIN
        self.btn2Pin = RIGHT_BTN_PIN
     
        # configure interrups for buttons
        GPIO.setup(self.btn1Pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(self.btn2Pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)  

        # configure screen
        spi_bus = 0
        spi_device = 0

        # Very important... This lets py-gaugette 'know' what pins to use in order to reset the display
        self.lcd = characterlcd.Character_LCD_Mono(lcd_rs, lcd_en, lcd_d4, lcd_d5, lcd_d6,
                                      lcd_d7, lcd_columns, lcd_rows)
        self.lcd.clear()
        # load the pump configuration from file
        self.pump_configuration = Bartender.readPumpConfiguration()
        for pump in self.pump_configuration.keys():
            GPIO.setup(self.pump_configuration[pump]["pin"], GPIO.OUT, initial=GPIO.HIGH)

        # setup pixels:
        self.numpixels = NUMBER_NEOPIXELS # Number of LEDs in strip

        # Here's how to control the strip from any two GPIO pins:
        datapin  = NEOPIXEL_DATA_PIN
        clockpin = NEOPIXEL_CLOCK_PIN
        #self.strip = Adafruit_DotStar(self.numpixels, datapin, clockpin)
        #self.strip.begin()           # Initialize pins for output
        #self.strip.setBrightness(NEOPIXEL_BRIGHTNESS) # Limit brightness to ~1/4 duty cycle

        # turn everything off
        #for i in range(0, self.numpixels):
        #    self.strip.setPixelColor(i, 0)
        #self.strip.show() 

        print("Done initializing")

    @staticmethod
    def readPumpConfiguration():
        return json.load(open('pump_config.json'))

    @staticmethod
    def writePumpConfiguration(configuration):
        with open("pump_config.json", "w") as jsonFile:
            json.dump(configuration, jsonFile)

    def startInterrupts(self):
        GPIO.add_event_detect(self.btn1Pin, GPIO.FALLING, callback=self.left_btn, bouncetime=LEFT_PIN_BOUNCE)  
        GPIO.add_event_detect(self.btn2Pin, GPIO.FALLING, callback=self.right_btn, bouncetime=RIGHT_PIN_BOUNCE)  

    def stopInterrupts(self):
        GPIO.remove_event_detect(self.btn1Pin)
        GPIO.remove_event_detect(self.btn2Pin)

    def buildMenu(self, drink_list, drink_options):
        # create a new main menu
        m = Menu("Main Menu")

        # add drink options
        drink_opts = []
        for d in drink_list:
            drink_opts.append(MenuItem('drink', d["name"], {"ingredients": d["ingredients"]}))

        configuration_menu = Menu("Configure")

        # add pump configuration options
        pump_opts = []
        for p in sorted(self.pump_configuration.keys()):
            config = Menu(self.pump_configuration[p]["name"])
            # add fluid options for each pump
            for opt in drink_options:
                # star the selected option
                selected = "*" if opt["value"] == self.pump_configuration[p]["value"] else ""
                config.addOption(MenuItem('pump_selection', opt["name"], {"key": p, "value": opt["value"], "name": opt["name"]}))
            # add a back button so the user can return without modifying
            config.addOption(Back("Back"))
            config.setParent(configuration_menu)
            pump_opts.append(config)

        # add pump menus to the configuration menu
        configuration_menu.addOptions(pump_opts)
        # add a back button to the configuration menu
        configuration_menu.addOption(Back("Back"))
        # adds an option that cleans all pumps to the configuration menu
        configuration_menu.addOption(MenuItem('clean', 'Clean'))
        configuration_menu.setParent(m)

        m.addOptions(drink_opts)
        m.addOption(configuration_menu)
        # create a menu context
        self.menuContext = MenuContext(m, self)

    def filterDrinks(self, menu):
        """
        Removes any drinks that can't be handled by the pump configuration
        """
        for i in menu.options:
            if (i.type == "drink"):
                i.visible = True
                ingredients = i.attributes["ingredients"]
                presentIng = 0
                for ing in ingredients.keys():
                    for p in self.pump_configuration.keys():
                        if (ing == self.pump_configuration[p]["value"]):
                            presentIng += 1
                if (presentIng == len(ingredients.keys())): 
                    i.visible = True
            elif (i.type == "menu"):
                self.filterDrinks(i)

    def selectConfigurations(self, menu):
        """
        Adds a selection star to the pump configuration option
        """
        for i in menu.options:
            if (i.type == "pump_selection"):
                key = i.attributes["key"]
                if (self.pump_configuration[key]["value"] == i.attributes["value"]):
                    i.name = "%s %s" % (i.attributes["name"], "*")
                else:
                    i.name = i.attributes["name"]
            elif (i.type == "menu"):
                self.selectConfigurations(i)

    def prepareForRender(self, menu):
        self.filterDrinks(menu)
        self.selectConfigurations(menu)
        return True

    def menuItemClicked(self, menuItem):
        if (menuItem.type == "drink"):
            self.makeDrink(menuItem.name, menuItem.attributes["ingredients"])
            return True
        elif(menuItem.type == "pump_selection"):
            self.pump_configuration[menuItem.attributes["key"]]["value"] = menuItem.attributes["value"]
            Bartender.writePumpConfiguration(self.pump_configuration)
            return True
        elif(menuItem.type == "clean"):
            self.clean()
            return True
        return False

    def clean(self):
        waitTime = 20
        pumpThreads = []

        # cancel any button presses while the drink is being made
        # self.stopInterrupts()
        self.running = True

        for pump in self.pump_configuration.keys():
            pump_t = threading.Thread(target=self.pour, args=(self.pump_configuration[pump]["pin"], waitTime))
            pumpThreads.append(pump_t)

        # start the pump threads
        for thread in pumpThreads:
            thread.start()

        # start the progress bar
        self.progressBar(waitTime)

        # wait for threads to finish
        for thread in pumpThreads:
            thread.join()

        # show the main menu
        self.menuContext.showMenu()

        # sleep for a couple seconds to make sure the interrupts don't get triggered
        time.sleep(2);

        # reenable interrupts
        # self.startInterrupts()
        self.running = False

    def displayMenuItem(self, menuItem):
        print("DRINK: " + menuItem.name)
        self.lcd.clear()
        self.lcd.message = menuItem.name

    def cycleLights(self):
        t = threading.currentThread()
        head  = 0               # Index of first 'on' pixel
        tail  = -10             # Index of last 'off' pixel
        color = 0xFF0000        # 'On' color (starts red)

        while getattr(t, "do_run", True):
            #self.strip.setPixelColor(head, color) # Turn on 'head' pixel
            #self.strip.setPixelColor(tail, 0)     # Turn off 'tail'
            #self.strip.show()                     # Refresh strip
            time.sleep(1.0 / 50)             # Pause 20 milliseconds (~50 fps)

            head += 1                        # Advance head position
            if(head >= self.numpixels):           # Off end of strip?
                head    = 0              # Reset to start
                color >>= 8              # Red->green->blue->black
                if(color == 0): color = 0xFF0000 # If black, reset to red

            tail += 1                        # Advance tail position
            if(tail >= self.numpixels): tail = 0  # Off end? Reset

    def lightsEndingSequence(self):
        # make lights green
        #for i in range(0, self.numpixels):
            #self.strip.setPixelColor(i, 0xFF0000)
        #self.strip.show()

        time.sleep(5)

        # turn lights off
        #for i in range(0, self.numpixels):
            #self.strip.setPixelColor(i, 0)
        #self.strip.show() 

    def pour(self, pin, waitTime):
        GPIO.output(pin, GPIO.LOW)
        time.sleep(waitTime)
        GPIO.output(pin, GPIO.HIGH)

    def progressBar(self, waitTime):
        interval = waitTime / 100.0
        for x in range(1, 101):
            self.lcd.clear()
            self.updateProgressBar(x)
            time.sleep(interval)

    def makeDrink(self, drink, ingredients):
        # cancel any button presses while the drink is being made
        # self.stopInterrupts()
        self.running = True

        # launch a thread to control lighting
        lightsThread = threading.Thread(target=self.cycleLights)
        lightsThread.start()

        # Parse the drink ingredients and spawn threads for pumps
        maxTime = 0
        pumpThreads = []
        for ing in ingredients.keys():
            for pump in self.pump_configuration.keys():
                if ing == self.pump_configuration[pump]["value"]:
                    waitTime = ingredients[ing] * FLOW_RATE
                    if (waitTime > maxTime):
                        maxTime = waitTime
                    pump_t = threading.Thread(target=self.pour, args=(self.pump_configuration[pump]["pin"], waitTime))
                    pumpThreads.append(pump_t)

        # start the pump threads
        for thread in pumpThreads:
            thread.start()

        # start the progress bar
        self.progressBar(maxTime)

        # wait for threads to finish
        for thread in pumpThreads:
            thread.join()

        # show the main menu
        self.menuContext.showMenu()

        # stop the light thread
        lightsThread.do_run = False
        lightsThread.join()

        # show the ending sequence lights
        self.lightsEndingSequence()

        # sleep for a couple seconds to make sure the interrupts don't get triggered
        time.sleep(2);

        # reenable interrupts
        # self.startInterrupts()
        self.running = False

    def left_btn(self, ctx):
        if not self.running:
            self.menuContext.advance()

    def right_btn(self, ctx):
        if not self.running:
            self.menuContext.select()

    def updateProgressBar(self, percent):
        print("procent:" + str(percent))
        self.lcd.clear()
        nr_of_characters = int(lcd_columns*percent/100)
        bar = ""
        for w in range(0, nr_of_characters):
            #bar += "█"
            bar += "#"
        print("Bar:" + bar)
        self.lcd.message = bar

    def run(self):
        self.startInterrupts()
        # main loop
        try:  
            while True:
                time.sleep(0.1)
          
        except KeyboardInterrupt:  
            GPIO.cleanup()       # clean up GPIO on CTRL+C exit  
        GPIO.cleanup()           # clean up GPIO on normal exit 

        traceback.print_exc()


bartender = Bartender()
bartender.buildMenu(drink_list, drink_options)
bartender.run()



