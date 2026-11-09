import azure.cognitiveservices.speech as speechsdk
import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]   # src/ → project root


load_dotenv(PROJECT_ROOT / '.env')

speech_key = os.environ.get("SUBSCRIPTION_KEY")
speech_region = os.environ.get("SERVICE_REGION")
speech_endpoint = os.environ.get("SERVICE_ENDPOINT")

config = speechsdk.SpeechConfig(
    subscription=speech_key,
    region=speech_region,
    #endpoint=speech_endpoint,
    speech_recognition_language="en-US"
)

# Create a recognizer with the given settings
speech_recognizer = speechsdk.SpeechRecognizer(speech_config=config)

print("Say something...")

result = speech_recognizer.recognize_once()

if result.reason == speechsdk.ResultReason.RecognizedSpeech:
    print("Recognized: {}".format(result.text))
elif result.reason == speechsdk.ResultReason.NoMatch:
    print("No speech could be rcognized: {}".format(result.no_match_details))
elif result.reason == speechsdk.ResultReason.Canceled:
    cancellation_details = result.cancellation_details
    print("Speech Recognition canceled: {}".format(cancellation_details.reason))
    if cancellation_details.reason == speechsdk.CancellationReason.Error:
        print("Error details: {}".format(cancellation_details.error_details))