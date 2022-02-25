#!/usr/bin/env python


import json
import random
import string

DICT_FILE = "/etc/dictionaries-common/words"


def get_words():
    words = []
    try:
        with open(DICT_FILE) as f:
            words.extend(f.read().split("\n"))
    except OSError:
        try:
            with open("LICENSE") as f:
                words.extend(
                    f.read()
                    .translate(string.maketrans("", ""), string.punctuation)
                    .split()
                )
        except OSError:
            print(
                json.dumps(
                    {"error": "couldn't open dictionary file", "filename": DICT_FILE}
                )
            )
    return words


def random_words(count=None, sig="me"):
    count = count or int(random.uniform(1, 500))
    words = get_words()
    random_word_list = []

    if sig:
        word_index = int(random.uniform(1, len(words)))
        random_word = words[word_index]

        salutation = ["Hey", "Hi", "Ahoy", "Yo"][int(random.uniform(0, 3))]
        random_word_list.append(f"{salutation} {random_word},\n\n")

    just_entered = False
    for i in range(count):
        word_index = int(random.uniform(1, len(words)))
        random_word = words[word_index]

        if i > 0 and not just_entered:
            random_word = " " + random_word

        just_entered = False

        if int(random.uniform(1, 15)) == 1:
            random_word += "."

            if int(random.uniform(1, 3)) == 1 and sig:
                random_word += "\n"
                just_entered = True

            if int(random.uniform(1, 3)) == 1 and sig:
                random_word += "\n"
                just_entered = True

        random_word_list.append(random_word)

    text = "".join(random_word_list) + "."
    if sig:
        if int(random.uniform(1, 2)) == 1:
            salutation = ["Cheers", "Adios", "Ciao", "Bye"][int(random.uniform(0, 3))]
            punct = [".", ",", "!", ""][int(random.uniform(0, 3))]
            text += f"\n\n{salutation}{punct}\n"
        else:
            text += "\n\n"

        punct = ["-", "- ", "--", "-- "][int(random.uniform(0, 3))]
        text += f"{punct}{sig}"

    return text


if __name__ == "__main__":
    print(random_words())
