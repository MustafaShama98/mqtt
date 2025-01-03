
import os
import json
import time
import base64
import asyncio
import ssl
import certifi
import cv2
import paho.mqtt.client as mqtt
import math
import numpy as np
# from picamera2 import Picamera2
import sys

# Global variables
mqtt_client = None
sys_id = None
waiting_for_sys_id = False
file_name = "system_data.json"


def calculate_painting_viewing_distance():
    """

    Calculate the optimal viewing distance for a painting.

    Parameters:
        diagonal (float): Diagonal size of the painting in cm.

    Returns:
        float: Optimal viewing distance in the same unit as the diagonal.
    """
    # Optimal distance is 2 times the diagonal size
    payload =  read_data()
    if payload:
        width =payload.get("width")
        height = payload.get("height")
        print(type(width))
        diagonal = math.sqrt(width**2 + height**2)
        print(f"distance {diagonal * 1.5}")
        return 1.5 * diagonal
    return None


def simulate_distance_sensor():
    """Simulate a distance sensor by allowing the user to input distance data."""
    global sys_id
    if not sys_id:
        print("No system ID set. Cannot publish sensor data.")
        return
    optimal_distance = calculate_painting_viewing_distance()
    in_range_flag = False  # Tracks whether a person is currently in range
    print(f"The range is 0 to {optimal_distance} to detect.")
    try:
        while True:
            user_distance = input("Enter the current distance (or type 'exit' to stop): ").strip()
            if user_distance.lower() == "exit":
                print("Exiting distance sensor simulation.")
                break

            # Validate input
            if not user_distance.isdigit():
                print("Invalid input. Please enter a numeric value for distance.")
                continue

            # Convert input to an integer
            distance = int(user_distance)

            # Check if the person is within the range of 0 to 150 cm
            if 0 <= distance <= optimal_distance:
                if not in_range_flag:  # Publish only if this is the first detection in range
                    in_range_flag = True
                    payload = {
                        "status": "person_detected", 
                        "distance": distance,
                        "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                        "device": "esp32"
                    }
                    mqtt_client.publish(f"m5stack/{sys_id}/sensor", json.dumps(payload), qos=2)
                    print(f"Person detected in range ({distance} cm). Published to MQTT.")
                else:
                    print(f"Person still in range ({distance} cm). No additional publish.")
            else:
                if in_range_flag:  # Publish only if the person was in range and is now out
                    in_range_flag = False
                    payload = {
                        "status": "person_out_of_range",
                        "distance": distance,
                        "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                        "device": "esp32"
                    }
                    mqtt_client.publish(f"m5stack/{sys_id}/sensor", json.dumps(payload), qos=2)
                    print(f"Person out of range ({distance} cm). Published to MQTT.")
                else:
                    print(f"No person in range ({distance} cm). No additional publish.")
    except KeyboardInterrupt:
        print("Stopped distance sensor simulation.")



# MQTT Callbacks
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("\nConnected to MQTT broker")
        payload = read_data()
        if not payload or not payload.get("sys_id"):
            client.subscribe("install", qos=2)
            print("Subscribed to /install topic")
        else:
            client.subscribe("status", qos=2)
            print("Subscribed to /status topic")
            handle_status_request()

    else:
        print(f"Connection failed with code {rc}")

def on_message(client, userdata, msg):
    print(f"\nReceived message on topic: {msg.topic}: {msg.payload.decode()}")
    try:
        payload = json.loads(msg.payload.decode())
        
        # Check for specific topic patterns
        if "get_frame" in msg.topic:
            handle_frame_request(msg.topic)
        elif "delete" in msg.topic:
            handle_deletion()
        elif "reset" in msg.topic:
            handle_reset()
        elif "status" in msg.topic:
            handle_status_request()
        elif "install" in msg.topic:
            handle_installation(payload)
        else:
            print(f"Unhandled topic: {msg.topic}")
    except Exception as e:
        print(f"Error processing message: {e}")

def on_disconnect(client, userdata, rc):
    print("Disconnected from MQTT broker with result code:", rc)
    print("Unexpected disconnection. Publishing 'Inactive' status.")
    client.publish(f"m5stack/{sys_id}/active", json.dumps({"status": False}), qos=1, retain=True)
    print("published inactive status")


#capture frame when using windows for dev and testing
def capture_frame():
    """Capture a frame from the default camera and return as base64."""
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        raise RuntimeError("Failed to open camera")

    ret, frame = cap.read()
    cap.release()
    if not ret:
        raise RuntimeError("Failed to capture frame")

    _, buffer = cv2.imencode(".jpg", frame)
    return base64.b64encode(buffer).decode("utf-8")

#capture frame when using rpi camera module v2
def capture_frame_rpi():
    from picamera2 import Picamera2

    picam2 = Picamera2()
    config = picam2.create_still_configuration(main={"size": (1280, 720)})  # Smaller resolution
    picam2.configure(config)

    picam2.start()
    frame = picam2.capture_array()
    picam2.stop()
    picam2.close()  # Ensure the camera is properly released

    # Resize the frame
    frame = cv2.resize(frame, (1920, 1080))  # Adjust to smaller size

    # Encode the frame as JPEG
    _, buffer = cv2.imencode(".jpg", frame)
    return base64.b64encode(buffer).decode("utf-8")

async def write_to_json_file_async(data):
    """Asynchronously write data to a JSON file."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: json.dump(data, open(file_name, "w"), indent=4))

async def read_from_json_file_async():
    """Asynchronously read data from a JSON file."""
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, lambda: json.load(open(file_name, "r")))
    except (FileNotFoundError, json.JSONDecodeError):
        return None

def read_data():
    """Read data synchronously from JSON file."""
    # file_name = os.path.join(os.getcwd(), "system_data.json")
    return asyncio.run(read_from_json_file_async())

def subscribe_to_sys_id_topics():
    """Subscribe to topics for the current sys_id."""
    if sys_id:
        mqtt_client.subscribe(f"m5stack/{sys_id}/delete", qos=2)
        mqtt_client.subscribe(f"m5stack/{sys_id}/get_frame", qos=2)
        mqtt_client.subscribe(f"m5stack/{sys_id}/height", qos=2)
        mqtt_client.subscribe(f"status", qos=2)

        print(f"Subscribed to delete, getframe, height, status")


# Handlers
def handle_installation(payload):
    global sys_id
    sys_id = payload.get("sys_id")
    print(f"System installed with sys_id: {sys_id}")
    mqtt_client.publish(f"m5stack/{sys_id}/install", json.dumps({"device": "esp32", "success": True}), qos=2)
    asyncio.run(write_to_json_file_async(payload))
    mqtt_client.unsubscribe("install")
    subscribe_to_sys_id_topics()

def handle_frame_request(topic):
    """Handle frame requests."""
    global sys_id
    _, sys_id, _ = topic.split("/")
    # Determine the operating system
    if os.name == "posix":
        base64_image = capture_frame_rpi()
    else:
        base64_image = capture_frame()
    print(f"Payload size: {len(base64_image)} bytes")
    payload = {"frameData": base64_image, "timestamp": time.time()}
    response_topic = f"m5stack/{sys_id}/frame_response"
    mqtt_client.publish(response_topic, json.dumps(payload), qos=2)
    print(f"Published frame data to {response_topic}")

def handle_deletion():
    global sys_id
    sys_id = None
    try:
        os.remove(file_name)
        print("System deleted and file removed.")
        mqtt_client.subscribe("install", qos=2)
        print("Subscribed to /install topic")
    except FileNotFoundError:
        print("File not found. No system data to delete.")

def handle_reset():
    global sys_id
    sys_id = None
    print("System reset.")

def handle_status_request():
    print(f"System {'is active' if sys_id else 'is not installed'} with sys_id: {sys_id or 'N/A'}")
    mqtt_client.publish(f"m5stack/{sys_id}/active", json.dumps({"status" : True}), qos=2)
    print("sent status active")

def publish_status_inactive():
    print(f"System {'is active' if sys_id else 'is not installed'} with sys_id: {sys_id or 'N/A'}")
    mqtt_client.publish(f"m5stack/{sys_id}/active", json.dumps({"status" : False}), qos=2)
    print("sent status inactive")


def initialize_sys_id():
    """Initialize sys_id from the JSON file if it exists."""
    global sys_id
    # file_name = os.path.join(os.getcwd(), file_name)
    try:
        payload = asyncio.run(read_from_json_file_async())
        if payload and "sys_id" in payload:
            sys_id = payload["sys_id"]
            print(f"Loaded sys_id from file: {sys_id}")
        else:
            print("No sys_id found in JSON file.")
    except Exception as e:
        print(f"Error initializing sys_id: {e}")




# MQTT Setup
def mqtt_setup():
    global mqtt_client
    print('MQTT setup!')
        # Configure the LWT (Last Will and Testament)

    mqtt_client = mqtt.Client(client_id=f"esp32_{hex(int(time.time() * 1000))[2:]}", protocol=mqtt.MQTTv311)
    mqtt_client.username_pw_set("art", "art123")
    context = ssl.create_default_context(cafile=certifi.where())
    mqtt_client.tls_set_context(context=context)
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message
    mqtt_client.on_disconnect = on_disconnect

    mqtt_client.will_set(
        topic=f"m5stack/{sys_id}/active",
        payload=json.dumps({"status": False}),
        qos=2,
        retain=True
    )

    mqtt_client.connect("j81f31b4.ala.eu-central-1.emqxsl.com", port=8883)
    mqtt_client.loop_start()

    # Subscribe to topics if sys_id is already set
    if sys_id:
        subscribe_to_sys_id_topics()

# Command Interface
def command_interface():
    global sys_id, waiting_for_sys_id
    if not sys.stdin.isatty():
        print("Running in non-interactive mode. Skipping command interface.")
        return
    commands = {
        "status": lambda: handle_status_request(),
        "reset": lambda: handle_reset(),
        "delete": lambda: handle_deletion(),
        "help": lambda: print("Available commands: status, reset, delete, set_id, sensor, exit"),
        "sensor": lambda: publish_sensor_data() if sys_id else print("No system ID set."),
        "simulate": lambda: simulate_distance_sensor() if sys_id else print("No system ID set."),
        "set_id": lambda: set_system_id(),
        "exit": lambda: exit_simulation(),
    }

    def set_system_id():
        global waiting_for_sys_id
        waiting_for_sys_id = True
        print("Enter the system ID:")

    def publish_sensor_data():
        payload = {"value": "50", "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), "device": "esp32"}
        mqtt_client.publish(f"m5stack/{sys_id}/sensor", json.dumps(payload), qos=2)
        print("Sensor data published.")

    def exit_simulation():
        print("Exiting...")
        mqtt_client.publish(f"m5stack/{sys_id}/active", json.dumps({"status": False}), qos=1, retain=True)
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        exit(0)

    while True:
        try:
            time.sleep(1)  # Sleep to reduce CPU usage
            user_input = input("\nEnter command: ").strip().lower()
            if waiting_for_sys_id:
                sys_id = user_input
                print(f"System ID manually set to: {sys_id}")
                waiting_for_sys_id = False
                subscribe_to_sys_id_topics()
                continue
            commands.get(user_input, lambda: print("Unknown command. Type 'help' for commands."))()
        except KeyboardInterrupt:
            exit_simulation()

def main():
    calculate_painting_viewing_distance()
    initialize_sys_id()  # Load sys_id from file
    mqtt_setup()
    print(os.name)
    command_interface()
    

if __name__ == "__main__":
    main()


