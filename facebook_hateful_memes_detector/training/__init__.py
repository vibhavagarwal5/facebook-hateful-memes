import numpy as np
import torch.nn as nn
import torch
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader
from torchvision import transforms, utils
import re
import contractions
import pandas as pd

from ..utils import in_notebook
from ..preprocessing import StratifiedSampler, my_collate
import gc


def train(model, optimizer, scheduler, batch_size, epochs, dataset, plot=False):
    if in_notebook():
        from tqdm.notebook import tqdm, trange
    else:
        from tqdm import tqdm as tqdm, trange

    _ = model.train()
    training_fold_labels = torch.tensor([dataset[i][2] for i in range(len(dataset))])
    sampler = StratifiedSampler(training_fold_labels, batch_size)
    train_loader = DataLoader(dataset, batch_size=batch_size, collate_fn=my_collate,
                              shuffle=False, num_workers=32, pin_memory=True, sampler=sampler)
    train_losses = []
    learning_rates = []
    try:
        with trange(epochs) as epo:
            for epoch in epo:
                _ = gc.collect()
                with tqdm(train_loader) as data_batch:
                    for texts, images, labels in data_batch:
                        optimizer.zero_grad()
                        logits, loss = model.forward(texts, images, labels)
                        loss.backward()
                        optimizer.step()
                        if scheduler is not None:
                            scheduler.step()
                        train_loss = loss.item() * labels.size(0)
                        train_losses.append(loss.item())
                        learning_rates.append(optimizer.param_groups[0]['lr'])

    except (KeyboardInterrupt, Exception) as e:
        epo.close()
        data_batch.close()
        raise
    epo.close()
    data_batch.close()
    import matplotlib.pyplot as plt

    if plot:
        t = list(range(len(train_losses)))

        fig, ax1 = plt.subplots()

        color = 'tab:red'
        ax1.set_xlabel('Training Batches')
        ax1.set_ylabel('Loss', color=color)
        ax1.plot(t, train_losses, color=color)
        ax1.tick_params(axis='y', labelcolor=color)

        ax2 = ax1.twinx()  # instantiate a second axes that shares the same x-axis

        color = 'tab:blue'
        ax2.set_ylabel('Learning Rate', color=color)  # we already handled the x-label with ax1
        ax2.plot(t, learning_rates, color=color)
        ax2.tick_params(axis='y', labelcolor=color)

        fig.tight_layout()  # otherwise the right y-label is slightly clipped
        plt.show()
    return train_losses, learning_rates


def validate(model, batch_size, dataset):
    from sklearn.metrics import roc_auc_score
    from sklearn.metrics import classification_report, precision_recall_fscore_support
    proba_list, predictions_list, labels_list = generate_predictions(model, batch_size, dataset)
    auc = roc_auc_score(labels_list, proba_list)
    p_micro, r_micro, f1_micro, _ = precision_recall_fscore_support(labels_list, predictions_list, average="micro")
    p_macro, r_macro, f1_macro, _ = precision_recall_fscore_support(labels_list, predictions_list, average="macro")
    p_weighted, r_weighted, f1_weighted, _ = precision_recall_fscore_support(labels_list, predictions_list,
                                                                             average="weighted")
    validation_scores = [f1_micro, f1_macro, f1_weighted, auc]
    # Confusion matrix
    return validation_scores


def generate_predictions(model, batch_size, dataset):
    _ = model.eval()
    proba_list = []
    predictions_list = []
    labels_list = []
    test_loader = DataLoader(dataset, batch_size=batch_size, collate_fn=my_collate,
                             shuffle=False, num_workers=32, pin_memory=True)
    with torch.no_grad():
        for texts, images, labels in test_loader:
            logits, valloss = model.forward(texts, images, labels)
            logits = torch.softmax(logits, dim=1)
            top_p, top_class = logits.topk(1, dim=1)
            labels = labels.tolist()
            labels_list.extend(labels)
            top_class = top_class.flatten().tolist()
            probas = logits[:, 1].tolist()
            predictions_list.extend(top_class)
            proba_list.extend(probas)
    return proba_list, predictions_list, labels_list


def model_builder(model_class, model_params,
                  optimiser_class=torch.optim.Adam, optimiser_params=dict(lr=0.001, weight_decay=1e-5),
                  scheduler_class=None, scheduler_params=None):
    def builder():
        model = model_class.build(**model_params)
        optimizer = optimiser_class(model.parameters(), **optimiser_params)
        scheduler = None
        if scheduler_class is not None:
            scheduler = scheduler_class(optimizer, **scheduler_params)
        return model, optimizer, scheduler

    return builder


def train_validate_ntimes(model_fn, data, n_tests, batch_size, epochs):
    if in_notebook():
        from tqdm.notebook import tqdm, trange
    else:
        from tqdm import tqdm as tqdm, trange
    results_list = []
    for _ in trange(n_tests):
        dataset = data["train"]
        size = len(dataset)
        training_fold_dataset, testing_fold_dataset = torch.utils.data.random_split(dataset, [int(size * 0.9),
                                                                                              size - int(size * 0.9)])
        model, optimizer, scheduler = model_fn()
        train_losses, learning_rates = train(model, optimizer, scheduler, batch_size, epochs, training_fold_dataset)

        validation_scores = validate(model, batch_size, testing_fold_dataset)
        train_scores = validate(model, batch_size, training_fold_dataset)
        index = ["f1_micro", "f1_macro", "f1_weighted", "auc"]
        rdf = pd.DataFrame(data=dict(train=train_scores, val=validation_scores), index=index)
        results_list.append(rdf)
    results = np.stack(results_list, axis=0)
    means = results.mean(0)
    stds = results.std(0)
    return means, stds


def train_and_predict(model_fn, data, batch_size, epochs):
    dataset = data["train"]
    model, optimizer, scheduler = model_fn()
    train_losses, learning_rates = train(model, optimizer, scheduler, batch_size, epochs, dataset, plot=True)
    test_dataset = data["test"]
    proba_list, predictions_list, labels_list = generate_predictions(model, batch_size, test_dataset)
    test = data["test_df"]
    submission = pd.DataFrame(dict(id=test.id, proba=proba_list, label=predictions_list),
                              columns=["id", "proba", "label"])
    return submission
