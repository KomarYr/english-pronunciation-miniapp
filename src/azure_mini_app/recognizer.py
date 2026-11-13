import azure.cognitiveservices.speech as speechsdk

from azure_mini_app.settings import (
    _require_azure_credentials,
    language,
    speech_endpoint,
    speech_key,
)


def pronunciation_assessment_from_microphone():
    _require_azure_credentials()
    config = speechsdk.SpeechConfig(
        subscription=speech_key,
        endpoint=speech_endpoint,
    )
    # Set longer break in reading.
    config.set_property(
        speechsdk.PropertyId.SpeechServiceConnection_EndSilenceTimeoutMs, "3000"
    )

    reference_text = ""
    pronunciation_config = speechsdk.PronunciationAssessmentConfig(
        reference_text=reference_text,
        grading_system=speechsdk.PronunciationAssessmentGradingSystem.HundredMark,
        granularity=speechsdk.PronunciationAssessmentGranularity.Phoneme,
        enable_miscue=True,
    )
    pronunciation_config.enable_prosody_assessment()

    # Create a recognizer with the given settings
    recognizer = speechsdk.SpeechRecognizer(speech_config=config, language=language)
    recognizer.session_started.connect(
        lambda evt: print(f"SESSION ID: {evt.session_id}")
    )
    pronunciation_config.apply_to(recognizer)

    while True:
        # Receives reference text from console input.
        print("Enter reference text you want to assess, or enter empty text to exit.")
        print("> ", end="")

        try:
            reference_text = input()
        except EOFError:
            break

        pronunciation_config.reference_text = reference_text

        # Start recognizing.
        print(f"Read out '{reference_text}' for pronunciation assessment ...")

        # For long-running multi-utterance pronunciation evaluation, use start_continuous_recognition()
        result = recognizer.recognize_once_async().get()

        # Check the result
        if result.reason == speechsdk.ResultReason.RecognizedSpeech:
            print(f"Recognized: {result.text}")
            print("    Pronunciation Assessment Result: ")

            pronunciation_result = speechsdk.PronunciationAssessmentResult(result)
            print(
                f"    Accuracy score: {pronunciation_result.accuracy_score}, "
                f"Prosody score: {pronunciation_result.prosody_score}, "
                f"FluencyScore: {pronunciation_result.fluency_score}, "
                f"Completeness score: {pronunciation_result.completeness_score}, "
                f"Pronunciation score: {pronunciation_result.pronunciation_score}"
            )
            print(" Word-level details:")
            for idx, word in enumerate(pronunciation_result.words):
                print(
                    f"    {idx + 1}: word: {word.word}\taccuracy score: {word.accuracy_score}\t"
                    f"error type: {word.error_type}"
                )
        elif result.reason == speechsdk.ResultReason.NoMatch:
            print("No speech could be recognized")
        elif result.reason == speechsdk.ResultReason.Canceled:
            cancellation_details = result.cancellation_details
            print(f"Speech Recognition canceled: {cancellation_details.reason}")
            if cancellation_details.reason == speechsdk.CancellationReason.Error:
                print(f"Error details: {cancellation_details.error_details}")


def main() -> None:
    pronunciation_assessment_from_microphone()


if __name__ == "__main__":
    main()
