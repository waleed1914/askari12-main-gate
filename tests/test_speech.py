from askari_vms.speech import (
    choose_input_device,
    default_model_path,
    normalize_spoken_numbers,
)


def test_usb_microphone_is_selected_by_name_not_unstable_index() -> None:
    devices = [
        {"name": "Microphone (Headset)", "max_input_channels": 1},
        {"name": "Output", "max_input_channels": 0},
        {"name": "Microphone (USB Microphone)", "max_input_channels": 1},
    ]
    assert choose_input_device(devices) == 2


def test_model_lives_beside_the_local_data() -> None:
    assert str(default_model_path(r"C:\AskariVMS")).endswith("vosk-model-small-en-us-0.15")


def test_spoken_digits_are_joined_for_house_numbers() -> None:
    assert normalize_spoken_numbers("block b house four zero seven") == "block b house 407"


def test_normal_number_phrases_are_converted() -> None:
    assert normalize_spoken_numbers("street twenty five phase seven") == "street 25 phase 7"
    assert normalize_spoken_numbers("house one hundred and five") == "house 105"


def test_non_number_destination_words_are_unchanged() -> None:
    assert normalize_spoken_numbers("officers mess and club") == "officers mess and club"
