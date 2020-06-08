from typing import List, Tuple, Dict, Set, Union
import numpy as np
import torch.nn as nn
import torch
import torchnlp
import torch.nn.functional as F
from .BaseClassifier import BaseClassifier
from ...utils import init_fc, GaussianNoise
import math
from .CNN1DClassifier import Residual1DConv


class PositionalEncoding(nn.Module):
    r"""Inject some information about the relative or absolute position of the tokens
        in the sequence. The positional encodings have the same dimension as
        the embeddings, so that the two can be summed. Here, we use sine and cosine
        functions of different frequencies.
    .. math::
        \text{PosEncoder}(pos, 2i) = sin(pos/10000^(2i/d_model))
        \text{PosEncoder}(pos, 2i+1) = cos(pos/10000^(2i/d_model))
        \text{where pos is the word position and i is the embed idx)
    Args:
        d_model: the embed dim (required).
        dropout: the dropout value (default=0.1).
        max_len: the max. length of the incoming sequence (default=5000).
    Examples:
        >>> pos_encoder = PositionalEncoding(d_model)
    """

    def __init__(self, d_model, dropout=0.1, max_len=5000):
        super(PositionalEncoding, self).__init__()
        self.dropout = nn.Dropout(p=dropout)

        pe = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        pe = pe.unsqueeze(0).transpose(0, 1)
        self.register_buffer('pe', pe)

    def forward(self, x):
        r"""Inputs of forward function
        Args:
            x: the sequence fed to the positional encoder model (required).
        Shape:
            x: [sequence length, batch size, embed dim]
            output: [sequence length, batch size, embed dim]
        Examples:
            >>> output = pos_encoder(x)
        """

        x = x + self.pe[:x.size(0), :]
        return self.dropout(x)


class PositionalEncoding2D(nn.Module):
    r"""Inject some information about the relative or absolute position of the tokens
        in the sequence. The positional encodings have the same dimension as
        the embeddings, so that the two can be summed. Here, we use sine and cosine
        functions of different frequencies.
    .. math::
        \text{PosEncoder}(pos, 2i) = sin(pos/10000^(2i/d_model))
        \text{PosEncoder}(pos, 2i+1) = cos(pos/10000^(2i/d_model))
        \text{where pos is the word position and i is the embed idx)
    Args:
        d_model: the embed dim (required).
        dropout: the dropout value (default=0.1).
        max_len: the max. length of the incoming sequence (default=5000).
    Examples:
        >>> pos_encoder = PositionalEncoding2D(d_model)
    """

    def __init__(self, d_model, dropout=0.1, max_len=5000):
        super(PositionalEncoding2D, self).__init__()
        self.dropout = nn.Dropout(p=dropout)

        pe = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        pe = pe.unsqueeze(0).transpose(0, 1)
        self.register_buffer('pe', pe)
        self.d_model = d_model

    def forward(self, x):
        r"""Inputs of forward function
        Args:
            x: the 2D image features fed to the positional encoder model (required).
        Shape:
            x: [batch size, C, H, W,]
            output: [HxW, batch size, C]
        Examples:
            >>> output = pos_encoder(x)
        """
        b = x.size(0)
        assert b.size(1) == self.d_model
        x = x.transpose(1, 2).transpose(2, 3) # B, H, W, C
        x = x.transpose(0, 1).transpose(1, 2) # H, W, B, C
        pe = self.pe[:x.size(0), :] # H, C
        pe = pe.unsqueeze(1).expand(pe.size(0), b, self.d_model) # H, B, C
        pe1 = pe.unsqueeze(1)
        pe2 = pe.unsqueeze(0)
        x = x + pe1 + pe2
        x = x.flatten(0, 1)
        return self.dropout(x)


class TransformerClassifier(BaseClassifier):
    def __init__(self, num_classes, n_tokens_in, n_channels_in, n_tokens_out, n_channels_out,
                 n_internal_dims, n_layers,
                 gaussian_noise=0.0, dropout=0.0):
        super(TransformerClassifier, self).__init__(num_classes, n_tokens_in, n_channels_in, n_tokens_out,
                                                    n_channels_out,
                                                    n_internal_dims, n_layers, gaussian_noise, dropout)
        assert math.log2(self.num_pooling).is_integer()
        gn = GaussianNoise(gaussian_noise)
        dp = nn.Dropout(dropout)
        self.decoder_query = nn.Parameter(torch.randn((n_tokens_out, n_internal_dims)) * (1 / n_internal_dims), requires_grad=True)

        input_nn1 = nn.Linear(n_channels_in, n_channels_in * 2)
        init_fc(input_nn1, "leaky_relu")
        input_nn2 = nn.Linear(n_channels_in * 2, n_internal_dims)
        init_fc(input_nn2, "linear")
        self.input_nn = nn.Sequential(dp, input_nn1, nn.LeakyReLU(), gn, input_nn2)

        output_nn1 = nn.Linear(n_internal_dims, n_internal_dims * 2)
        init_fc(output_nn1, "leaky_relu")
        output_nn2 = nn.Linear(n_internal_dims * 2, n_channels_out)
        init_fc(output_nn2, "linear")
        self.output_nn = nn.Sequential(dp, output_nn1, nn.LeakyReLU(), gn, output_nn2)

        self.transformer = nn.Transformer(n_internal_dims, 16, n_layers, n_layers, n_internal_dims*4, dropout)

        classifier_nn1 = nn.Linear(n_channels_out, n_channels_out * 2)
        init_fc(classifier_nn1, "leaky_relu")
        classifier_nn2 = nn.Linear(n_channels_out * 2, num_classes)
        init_fc(classifier_nn2, "linear")
        self.classifier_nn = nn.Sequential(dp, classifier_nn1, nn.LeakyReLU(), classifier_nn2)
        self.pos_encoder = PositionalEncoding(n_internal_dims, dropout)

    def forward(self, x):
        x = self.input_nn(x)

        x = x.transpose(0, 1)
        x = self.pos_encoder(x)
        batch_size = x.size(1)
        transformer_tgt = self.decoder_query.unsqueeze(0).expand(batch_size, *self.decoder_query.size())
        transformer_tgt = transformer_tgt.transpose(0, 1)
        x = self.transformer(x, transformer_tgt)
        x = x.transpose(0, 1)

        x = self.output_nn(x)
        logits = self.classifier_nn(x.mean(1))
        assert x.size(1) == self.n_tokens_out and x.size(2) == self.n_channels_out
        return logits, x


class TransformerEnsembleClassifier(nn.Module):
    def __init__(self, ensemble_config: Dict[str, Dict[str, object]],
                 num_classes, n_tokens_out, n_channels_out,
                 n_internal_dims, n_layers,
                 gaussian_noise=0.0, dropout=0.0):
        super(TransformerEnsembleClassifier, self).__init__()
        assert math.log2(self.num_pooling).is_integer()
        gn = GaussianNoise(gaussian_noise)
        dp = nn.Dropout(dropout)
        self.decoder_query = nn.Parameter(torch.randn((n_tokens_out, n_internal_dims)) * (1 / n_internal_dims),
                                          requires_grad=True)

        self.ensemble_config = ensemble_config
        ensemble_inp = dict()
        ensemble_id = dict()
        # n_tokens_in n_channels_in is2d
        for i, (k, v) in enumerate(ensemble_config.items()):
            is2d, n_tokens_in, n_channels_in = v["is2d"], v["n_tokens_in"], v["n_channels_in"]
            # input_nn, embedding, position,
            input_nn1 = nn.Linear(n_channels_in, n_channels_in * 2)
            init_fc(input_nn1, "leaky_relu")
            input_nn2 = nn.Linear(n_channels_in * 2, n_internal_dims)
            init_fc(input_nn2, "linear")
            input_nn = nn.Sequential(dp, input_nn1, nn.LeakyReLU(), gn, input_nn2)
            ensemble_inp[k] = input_nn
            ensemble_id[k] = i
        self.ensemble_inp = nn.ModuleDict(ensemble_inp)
        self.ensemble_id = ensemble_id
        self.em = nn.Embedding(len(ensemble_config), n_internal_dims)
        nn.init.normal_(self.em.weight, std=1 / n_internal_dims)

        output_nn1 = nn.Linear(n_internal_dims, n_internal_dims * 2)
        init_fc(output_nn1, "leaky_relu")
        output_nn2 = nn.Linear(n_internal_dims * 2, n_channels_out)
        init_fc(output_nn2, "linear")
        self.output_nn = nn.Sequential(dp, output_nn1, nn.LeakyReLU(), gn, output_nn2)

        self.transformer = nn.Transformer(n_internal_dims, 16, n_layers, n_layers, n_internal_dims * 4, dropout)

        classifier_nn1 = nn.Linear(n_channels_out, n_channels_out * 2)
        init_fc(classifier_nn1, "leaky_relu")
        classifier_nn2 = nn.Linear(n_channels_out * 2, num_classes)
        init_fc(classifier_nn2, "linear")
        self.classifier_nn = nn.Sequential(dp, classifier_nn1, nn.LeakyReLU(), classifier_nn2)
        self.pos_encoder = PositionalEncoding(n_internal_dims, dropout)
        self.pos_encoder2d = PositionalEncoding2D(n_internal_dims, dropout)

    def forward(self, idict: Dict[str, torch.Tensor]):
        vecs = []
        for k, v in idict.items():
            v = self.ensemble_inp[k](v)
            if self.ensemble_config[k]["is2d"]:
                v = self.pos_encoder2d(v)
            else:
                v = v.transpose(0, 1)
            seq_label = self.em(self.ensemble_id[k])
            v = v + seq_label
            vecs.append(v)
        x = torch.cat(vecs, 0)
        x = self.pos_encoder(x)
        batch_size = x.size(1)
        transformer_tgt = self.decoder_query.unsqueeze(0).expand(batch_size, *self.decoder_query.size())
        transformer_tgt = transformer_tgt.transpose(0, 1)
        x = self.transformer(x, transformer_tgt)
        x = x.transpose(0, 1)

        x = self.output_nn(x)
        logits = self.classifier_nn(x.mean(1))
        assert x.size(1) == self.n_tokens_out and x.size(2) == self.n_channels_out
        return logits, x



