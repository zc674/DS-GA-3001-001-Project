import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

from ts.utils.helper_funcs import load, save
from ts.utils.logger import Logger
from ts.utils.loss_modules import PinballLoss


class BaseTrainer(nn.Module):
    def __init__(self, model_name, model, dataloader, run_id, config, ohe_headers, csv_path, reload):
        super(BaseTrainer, self).__init__()
        self.model_name = model_name
        self.model = model.to(config["device"])
        self.config = config
        self.data_loader = dataloader
        self.ohe_headers = ohe_headers
        self.optimizer = torch.optim.Adam(self.model.parameters(), lr=config["learning_rate"])
        # self.optimizer = torch.optim.ASGD(self.model.parameters(), lr=config["learning_rate"])
        self.scheduler = torch.optim.lr_scheduler.StepLR(self.optimizer,
                                                         step_size=config["lr_anneal_step"],
                                                         gamma=config["lr_anneal_rate"])
        self.criterion = PinballLoss(self.config["training_tau"],
                                     self.config["output_size"] * self.config["batch_size"], self.config["device"])
        self.epochs = 0
        self.max_epochs = config["num_of_train_epochs"]
        self.run_id = str(run_id)
        self.prod_str = "prod" if config["prod"] else "dev"
        self.csv_save_path = csv_path
        logger_path = str(csv_path / ("tensorboard/" + self.model_name) / (
                        "train%s%s%s" % (self.config["variable"], self.prod_str, self.run_id)))
        self.log = Logger(logger_path)
        self.reload = reload

    def train_epochs(self):
        max_loss = 1e8
        start_time = time.time()
        file_path = Path(".") / ("models/" + self.model_name)
        if self.reload:
            load(file_path, self.model, self.optimizer)
        for e in range(self.max_epochs):
            epoch_loss = self.train()
            if epoch_loss < max_loss:
                print("Loss decreased, saving model!")
                file_path = Path(".") / ("models/" + self.model_name)
                save(file_path, self.model, self.optimizer)
                max_loss = epoch_loss
            file_path = self.csv_save_path / "grouped_results" / self.run_id / self.prod_str
            file_path_validation_loss = file_path / "validation_losses.csv"
            if e == 0:
                file_path.mkdir(parents=True, exist_ok=True)
                with open(file_path_validation_loss, "w") as f:
                    f.write("epoch,training_loss,validation_loss\n")
            epoch_val_loss = self.val(file_path)
            with open(file_path_validation_loss, "a") as f:
                f.write(",".join([str(e), str(epoch_loss), str(epoch_val_loss)]) + "\n")
            self.epochs += 1
        print("Total Training Mins: %5.2f" % ((time.time() - start_time) / 60))

    def train(self):
        self.model.train()
        epoch_loss = 0
        for batch_num, (train, val, test, info_cat, idx) in enumerate(self.data_loader):
            start = time.time()
            print("Train_batch: %d" % (batch_num + 1))
            loss = self.train_batch(train, val, test, info_cat, idx)
            epoch_loss += loss
            end = time.time()
            self.log.log_scalar("Iteration time", end - start, batch_num + 1 * (self.epochs + 1))
        epoch_loss = epoch_loss / (batch_num + 1)

        # LOG EPOCH LEVEL INFORMATION
        print("[TRAIN]  Epoch [%d/%d]   Loss: %.4f" % (
            self.epochs, self.max_epochs, epoch_loss))
        info = {"loss": epoch_loss}

        self.log_values(info)
        self.log_hists()

        return epoch_loss

    def train_batch(self, train, val, test, info_cat, idx):
        pass

    def val(self, file_path, testing):
        return 0

    def log_values(self, info):

        # SCALAR
        for tag, value in info.items():
            self.log.log_scalar(tag, value, self.epochs + 1)

    def log_hists(self):
        # HISTS
        batch_params = dict()
        for tag, value in self.model.named_parameters():
            if value.grad is not None:
                if "init" in tag:
                    name, _ = tag.split(".")
                    if name not in batch_params.keys() or "%s/grad" % name not in batch_params.keys():
                        batch_params[name] = []
                        batch_params["%s/grad" % name] = []
                    batch_params[name].append(value.data.cpu().numpy())
                    batch_params["%s/grad" % name].append(value.grad.cpu().numpy())
                else:
                    tag = tag.replace(".", "/")
                    self.log.log_histogram(tag, value.data.cpu().numpy(), self.epochs + 1)
                    self.log.log_histogram(tag + "/grad", value.grad.data.cpu().numpy(), self.epochs + 1)
            else:
                print("Not printing %s because it\"s not updating" % tag)

        for tag, v in batch_params.items():
            vals = np.concatenate(np.array(v))
            self.log.log_histogram(tag, vals, self.epochs + 1)