import abc
from typing import List, Tuple, Dict, Set, Union
import numpy as np
import torch.nn as nn
import torch
import torch.nn.functional as F
import fasttext

from ...utils import init_fc, clean_text


class FasttextPooledModel(nn.Module):
    def __init__(self, in_dims, hidden_dims, num_classes, fasttext_file=None, fasttext_model=None,):
        super(FasttextPooledModel, self).__init__()
        assert fasttext_file is not None or fasttext_model is not None
        if fasttext_file is not None:
            self.text_model = fasttext.load_model(fasttext_file)
        else:
            self.text_model = fasttext_model
        layers = [nn.Linear(in_dims, hidden_dims, bias=True), nn.LeakyReLU(), nn.Linear(hidden_dims, num_classes)]
        init_fc(layers[0], 'xavier_uniform_', "leaky_relu")
        init_fc(layers[2], 'xavier_uniform_', "linear")
        self.classifier = nn.Sequential(*layers)
        self.loss = nn.CrossEntropyLoss()
        self.num_classes = num_classes

    def get_sentence_vector(self, texts: List[str]):
        result = [list(self.text_model.get_sentence_vector(text)) for text in texts]
        for i, r in enumerate(result):
            if np.sum(r[0:5]) == 0:
                result[i] = list(np.random.randn(self.in_dims))
        return torch.tensor(result)

    def forward(self, texts: List[str], img=None, labels=None):
        logits = self.predict_proba(texts, img)
        if labels is not None and labels[0] is not None:
            labels = torch.tensor(labels)
            loss = self.loss(logits.view(-1, self.num_classes), labels.view(-1))
            return logits, loss
        else:
            return logits

    def predict(self, texts: List[str], img=None):
        logits = self.predict_proba(texts, img)
        return logits.max(dim=1).indices

    def predict_proba(self, texts: List[str], img=None):
        vectors = self.get_sentence_vector(texts)
        logits = self.classifier(vectors)
        return logits


