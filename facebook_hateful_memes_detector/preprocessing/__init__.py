import torch.nn as nn
import torch
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader
from torchvision import transforms, utils

import pandas as pd
import numpy as np
import jsonlines
import abc
from typing import List, Tuple, Dict, Set, Union
from PIL import Image
from ..utils import read_json_lines_into_df

import torch as th
import math
import os
import re
import contractions
from pycontractions import Contractions
from torch.utils.data.sampler import WeightedRandomSampler


def clean_text(text):
    # https://stackoverflow.com/questions/6202549/word-tokenization-using-python-regular-expressions
    # https://stackoverflow.com/questions/44263446/python-regex-to-add-space-after-dot-or-comma/44263500
    EMPTY = ' '
    assert text is not None
    text = re.sub(r'([A-Z][a-z]+(?=[A-Z]))', r'\1 ', text)
    text = re.sub(r'(?<=[.,;!])(?=[^\s0-9])', ' ', text)
    text = re.sub('[ ]+', ' ', text)

    def replace_link(match):
        return EMPTY if re.match('[a-z]+://', match.group(1)) else match.group(1)

    text = re.sub('<a[^>]*>(.*)</a>', replace_link, text)
    text = re.sub('<.*?>', EMPTY, text)
    text = re.sub('\[.*?\]', EMPTY, text)
    text = contractions.fix(text)
    text = text.lower()

    text = text.replace("'", " ").replace('"', " ")
    text = text.replace("\n", " ").replace("(", " ").replace(")", " ").replace("\r", " ").replace("\t", " ")

    text = re.sub('<pre><code>.*?</code></pre>', EMPTY, text)
    text = re.sub('<code>.*?</code>', EMPTY, text)

    text = re.sub(r"[^A-Za-z0-9.!$,;\'? ]+", EMPTY, text)
    text = " ".join([t.strip() for t in text.split()])
    return text


import os
import random
import fasttext
import gensim.downloader as api
from nltk import sent_tokenize
from gensim.models.fasttext import load_facebook_model
from nltk.corpus import stopwords


class TextAugment:
    def __init__(self, count_proba: List[float], choice_probas: Dict[str, int], fasttext_file: str = None):
        self.count_proba = count_proba
        assert 1 - 1e-6 <= sum(count_proba) <= 1 + 1e-6
        assert len(count_proba) >= 1
        assert (len(count_proba) - 1) < sum([v > 0 for v in choice_probas.values()])

        import nlpaug.augmenter.char as nac
        import nlpaug.augmenter.word as naw
        from gensim.similarities.index import AnnoyIndexer

        def one_third_cut(text):
            words = text.split()
            if len(words) <= 3:
                return text
            part = random.randint(0, 1)
            psize = int(len(words)/3)
            if bool(part):
                words = words[psize:]
            else:
                words = words[:len(words) - psize]
            return " ".join(words)

        def half_cut(text):
            words = text.split()
            if len(words) <= 2:
                return text
            part = random.randint(0, 1)
            psize = int(len(words)/2)
            if bool(part):
                words = words[psize:]
            else:
                words = words[:len(words) - psize]
            return " ".join(words)

        def sentence_shuffle(text):
            sents = sent_tokenize(text)
            random.shuffle(sents)
            return " ".join(sents)

        def text_rotate(text):
            words = text.split()
            if len(words) <= 2:
                return text
            rotate = random.randint(1, int(len(words)/2))
            words = words[rotate:] + words[:rotate]
            return " ".join(words)

        stopwords_list = stopwords.words("english")

        def stopword_insert(text):
            words = text.split()
            idx = random.randint(0, len(words) - 1)
            sw = random.sample(stopwords_list, 1)[0]
            words = words[:idx] + [sw] + words[idx:]
            return " ".join(words)

        def word_join(text):
            words = text.split()
            if len(words) <= 2:
                return text
            idx = random.randint(0, len(words) - 2)
            w1 = words[idx] + words[idx + 1]
            words = words[:idx] + [w1] + words[idx + 1:]
            return " ".join(words)

        def word_cutout(text):
            words = text.split()
            lwi = [i for i, w in enumerate(words) if len(w) >= 4]
            if len(lwi) <= 2:
                return text
            cut_idx = random.sample(lwi, 1)[0]
            words = words[:cut_idx] + words[cut_idx + 1:]
            return " ".join(words)

        self.augs = ["keyboard", "ocr", "char_insert", "char_substitute", "char_swap", "char_delete",
                     "word_insert", "word_substitute", "w2v_insert", "w2v_substitute", "text_rotate",
                     "stopword_insert", "word_join", "word_cutout",
                     "fasttext", "glove_twitter", "glove_wiki", "word2vec",
                     "synonym", "split", "sentence_shuffle", "one_third_cut", "half_cut"]
        assert len(set(list(choice_probas.keys())) - set(self.augs)) == 0
        self.augments = dict()
        self.indexes = dict()
        for k, v in choice_probas.items():
            if v <= 0:
                continue
            if k == "stopword_insert":
                self.augments["stopword_insert"] = stopword_insert
            if k == "word_join":
                self.augments["word_join"] = word_join
            if k == "word_cutout":
                self.augments["word_cutout"] = word_cutout
            if k == "text_rotate":
                self.augments["text_rotate"] = text_rotate
            if k == "sentence_shuffle":
                self.augments["sentence_shuffle"] = sentence_shuffle
            if k == "one_third_cut":
                self.augments["one_third_cut"] = one_third_cut
            if k == "half_cut":
                self.augments["half_cut"] = half_cut
            if k == "synonym":
                self.augments["synonym"] = naw.SynonymAug(aug_src='ppdb', model_path='ppdb-2.0-s-all', aug_max=1)
            if k == "split":
                self.augments["split"] = naw.SplitAug(aug_max=1, min_char=6,)

            if k == "fasttext":
                assert fasttext_file is not None
                self.augments["fasttext"] = load_facebook_model(fasttext_file)
                self.indexes["fasttext"] = AnnoyIndexer(self.augments["fasttext"], 8)
            if k == "word2vec":
                self.augments["word2vec"] = api.load("word2vec-google-news-300")
                self.indexes["word2vec"] = AnnoyIndexer(self.augments["word2vec"], 8)
            if k == "glove_twitter":
                self.augments["glove_twitter"] = api.load("glove-twitter-100")
                self.indexes["glove_twitter"] = AnnoyIndexer(self.augments["glove_twitter"], 8)
            if k == "glove_wiki":
                self.augments["glove_wiki"] = api.load("glove-wiki-gigaword-100")
                self.indexes["glove_wiki"] = AnnoyIndexer(self.augments["glove_wiki"], 8)

            if k == "keyboard":
                self.augments["keyboard"] = nac.KeyboardAug(aug_char_min=1, aug_char_max=3, aug_word_min=1,
                                                            aug_word_max=3, include_special_char=False,
                                                            include_numeric=False, include_upper_case=False)
            if k == "ocr":
                self.augments["ocr"] = nac.OcrAug(aug_char_min=1, aug_char_max=3, aug_word_min=1, aug_word_max=3, min_char=3)

            if k == "char_insert":
                self.augments["char_insert"] = nac.RandomCharAug(action="insert", aug_char_min=1, aug_char_max=2,
                                                                 aug_word_min=1, aug_word_max=3, include_numeric=False,
                                                                 include_upper_case=False)
            if k == "char_substitute":
                self.augments["char_substitute"] = nac.RandomCharAug(action="substitute", aug_char_min=1,
                                                                     aug_char_max=2, aug_word_min=1,
                                                                     aug_word_max=3, include_numeric=False,
                                                                     include_upper_case=False)
            if k == "char_swap":
                self.augments["char_swap"] = nac.RandomCharAug(action="swap", aug_char_min=1, aug_char_max=1,
                                                               aug_word_min=1,
                                                               aug_word_max=3, include_numeric=False,
                                                               include_upper_case=False)
            if k == "char_delete":
                self.augments["char_delete"] = nac.RandomCharAug(action="delete", aug_char_min=1, aug_char_max=1,
                                                                 aug_word_min=1,
                                                                 aug_word_max=3, include_numeric=False,
                                                                 include_upper_case=False)

            if k == "word_insert":
                self.augments["word_insert"] = naw.ContextualWordEmbsAug(model_path='distilbert-base-uncased',
                                                                         action='insert', temperature=0.5, top_k=20,
                                                                         aug_min=1, aug_max=1, optimize=True)
            if k == "word_substitute":
                self.augments["word_substitute"] = naw.ContextualWordEmbsAug(model_path='distilbert-base-uncased',
                                                                             action='substitute', temperature=0.5,
                                                                             top_k=20, aug_min=1, aug_max=1,
                                                                             optimize=True)
            if k == "w2v_insert":
                self.augments["w2v_insert"] = naw.WordEmbsAug(model_type='word2vec',
                                                              model_path='GoogleNews-vectors-negative300.bin',
                                                              action="insert", aug_min=1, aug_max=1, top_k=10, )
            if k == "w2v_substitute":
                self.augments["w2v_substitute"] = naw.WordEmbsAug(model_type='word2vec',
                                                                  model_path='GoogleNews-vectors-negative300.bin',
                                                                  action="substitute", aug_min=1, aug_max=1, top_k=10, )

        choices_arr = np.array([choice_probas[c] if c in choice_probas else 0.0 for c in self.augs])
        self.choice_probas = choices_arr / np.linalg.norm(choices_arr, ord=1)

    def __fasttext_replace__(self, tm, indexer, text):
        tokens = text.split()
        t_2_i = {w: i for i, w in enumerate(tokens) if len(w) >= 4}
        if len(t_2_i) <= 2:
            return text
        sampled = random.sample(list(t_2_i.keys()), k=1)[0]
        sampled_idx = t_2_i[sampled]
        # candidates = [w for d, w in self.augments["fasttext"].get_nearest_neighbors(sampled, 10)]
        candidates = [w for w, d in tm.most_similar(sampled, topn=10, indexer=indexer)][1:]
        replacement = random.sample(candidates, k=1)[0]
        tokens[sampled_idx] = replacement
        return " ".join(tokens)

    def __w2v_replace__(self, tm, indexer, text):
        tokens = text.split()
        t_2_i = {w: i for i, w in enumerate(tokens) if len(w) >= 4}
        if len(t_2_i) <= 2:
            return text
        success = False
        repeat_count = 0
        while not success and repeat_count <= 10:
            repeat_count += 1
            sampled = random.sample(list(t_2_i.keys()), k=1)[0]
            if sampled in tm:
                candidates = [w for w, d in tm.most_similar(sampled, topn=10, indexer=indexer)][1:]
                success = True
        if not success:
            return text
        sampled_idx = t_2_i[sampled]
        replacement = random.sample(candidates, k=1)[0]
        tokens[sampled_idx] = replacement
        return " ".join(tokens)

    def __call__(self, text):
        count = np.random.choice(list(range(len(self.count_proba))), 1, replace=False, p=self.count_proba)[0]
        augs = np.random.choice(self.augs, count, replace=False, p=self.choice_probas)
        for aug in augs:
            try:
                if aug == "fasttext":
                    text = self.__fasttext_replace__(self.augments[aug], self.indexes[aug], text)
                elif aug in ["glove_twitter", "glove_wiki", "word2vec"]:
                    text = self.__w2v_replace__(self.augments[aug], self.indexes[aug], text)
                elif aug in ["sentence_shuffle", "text_rotate", "stopword_insert", "word_join", "word_cutout",
                             "half_cut", "one_third_cut"]:
                    text = self.augments[aug](text)
                else:
                    text = self.augments[aug].augment(text)
            except Exception as e:
                print("Exception for: ", aug, "|", text, "|", augs, e)
        return text


def get_basic_image_transforms():
    preprocess = transforms.Compose([
        transforms.Resize(256),
        transforms.RandomCrop(224),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])
    return preprocess


def my_collate(batch):
    text = [item[0] for item in batch]
    image = torch.stack([item[1] for item in batch])
    label = [item[2] for item in batch]
    label = torch.LongTensor(label) if label[0] is not None else label
    sample_weights = torch.tensor([item[3] for item in batch], dtype=float)
    return [text, image, label, sample_weights]


def get_datasets(data_dir, train_text_transform=None, train_image_transform=None,
                 test_text_transform=None, test_image_transform=None,
                 cache_images: bool = True, use_images: bool = True, dev: bool = False):
    use_dev = dev
    from functools import partial
    joiner = partial(os.path.join, data_dir)
    dev = read_json_lines_into_df(joiner('dev.jsonl'))
    train = read_json_lines_into_df(joiner('train.jsonl'))
    test = read_json_lines_into_df(joiner('test.jsonl'))

    dev["img"] = list(map(joiner, dev.img))
    train["img"] = list(map(joiner, train.img))
    test["img"] = list(map(joiner, test.img))

    augmented_data = os.path.exists(joiner("train-augmented.csv"))
    train_augmented = pd.read_csv(joiner("train-augmented.csv")) if os.path.exists(joiner("train-augmented.csv")) else None
    dev_augmented = pd.read_csv(joiner("dev-augmented.csv")) if os.path.exists(joiner("dev-augmented.csv")) else None
    test_augmented = pd.read_csv(joiner("test-augmented.csv")) if os.path.exists(joiner("test-augmented.csv")) else None
    submission_format = pd.read_csv(joiner("submission_format.csv"))
    # TODO: Fold in dev into train
    if use_dev:
        train_augmented = dev_augmented
        train = dev
    else:
        train = pd.concat((train, dev))
        train_augmented = pd.concat((train_augmented, dev_augmented)) if train_augmented is not None else None

    rd = dict(train=train, test=test, dev=dev,
              train_augmented=train_augmented, dev_augmented=dev_augmented, test_augmented=test_augmented,
              submission_format=submission_format,
              metadata=dict(cache_images=cache_images, use_images=use_images, dev=use_dev,
                            train_text_transform=train_text_transform, train_image_transform=train_image_transform,
                            test_text_transform=test_text_transform, test_image_transform=test_image_transform,
                            data_dir=data_dir, augmented_data=augmented_data))
    return rd


def make_weights_for_balanced_classes(labels, weight_per_class: Dict = None):
    labels = labels.numpy()
    from collections import Counter
    count = Counter(labels)
    N = len(labels)
    if weight_per_class is None:
        weight_per_class = {clas: N / float(occ) for clas, occ in count.items()}
    weight = [weight_per_class[label] for label in labels]
    return torch.DoubleTensor(weight)


class TextImageDataset(Dataset):
    def __init__(self, texts: List[str], image_locations: List[str], labels: torch.Tensor = None,
                 sample_weights: List[float] = None,
                 text_transform=None, image_transform=None, cache_images: bool = True, use_images: bool = True):
        self.texts = [clean_text(text) for text in texts]
        self.image_locations = image_locations
        if use_images:
            self.images = {l: Image.open(l).convert('RGB') for l in image_locations} if cache_images else dict()
        self.labels = labels
        self.text_transform = text_transform
        self.image_transform = image_transform
        self.use_images = use_images
        self.sample_weights = [1.0] * len(texts) if sample_weights is None else sample_weights
        assert len(self.sample_weights) == len(self.image_locations) == len(self.texts)

    def __getitem__(self, item):
        text = self.texts[item]
        label = self.labels[item] if self.labels is not None else 0
        sample_weight = self.sample_weights[item]
        if self.text_transform is not None:
            text = self.text_transform(text)
        if self.use_images:
            l = self.image_locations[item]
            image = self.images.get(l)
            if image is None:
                image = Image.open(l).convert('RGB')
            image = self.image_transform(image) if self.image_transform is not None else image
            return text, image, label, sample_weight
        else:
            return text, torch.tensor(0), label, sample_weight

    def __len__(self):
        return len(self.texts)

    def show(self, item):
        l = self.image_locations[item]
        image = Image.open(l)
        text = self.texts[item]
        label = self.labels[item] if self.labels is not None else None
        print(text, "|", "Label = ", label)
        image.show()
        return image
