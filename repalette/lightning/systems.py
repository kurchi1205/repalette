import torch
import pytorch_lightning as pl
from pytorch_lightning.metrics.regression import (
    MeanSquaredError,
)
from torch import nn as nn

from repalette.constants import (
    DEFAULT_GENERATOR_LR,
    DEFAULT_DISCRIMINATOR_LR,
    DEFAULT_ADVERSARIAL_BETA_1,
    DEFAULT_ADVERSARIAL_BETA_2,
    DEFAULT_GENERATOR_WEIGHT_DECAY,
    DEFAULT_DISCRIMINATOR_WEIGHT_DECAY,
    DEFAULT_ADVERSARIAL_LAMBDA_MSE_LOSS,
)
from repalette.models import (
    PaletteNet,
    Discriminator,
)
from repalette.utils.transforms import LABNormalizer


class PreTrainSystem(pl.LightningModule):
    """
    Wrapper for pre-training of PaletteNet.
    """

    def __init__(
        self,
        learning_rate,
        beta_1,
        beta_2,
        weight_decay,
        optimizer,
        batch_size,
        multiplier,
        scheduler_patience,
    ):
        super().__init__()

        # manually configure hyperparams in case we add some non-hyperparams arguments in the future
        # they are saved to `self.hparams`
        self.save_hyperparameters(
            "learning_rate",
            "beta_1",
            "beta_2",
            "weight_decay",
            "optimizer",
            "batch_size",
            "multiplier",
            "scheduler_patience",
        )

        self.generator = PaletteNet()
        # self.MSE = MeanSquaredError()
        self.MSE = torch.nn.MSELoss()
        self.normalizer = LABNormalizer()

    def forward(self, img, palette):
        return self.generator(img, palette)

    def training_step(self, batch, batch_idx):
        (source_img, _), (
            target_img,
            target_palette,
        ) = batch
        target_palette = nn.Flatten()(target_palette)
        recolored_img_ab = self.generator(source_img, target_palette)
        loss = self.MSE(
            recolored_img_ab,
            target_img[:, 1:, :, :],
        )
        self.log("Train/loss", loss, on_epoch=True)

        return loss

    def validation_step(self, batch, batch_idx):
        (source_img, _), (
            target_img,
            target_palette,
        ) = batch
        target_palette = nn.Flatten()(target_palette)
        recolored_img_ab = self.generator(source_img, target_palette)
        loss = self.MSE(
            recolored_img_ab,
            target_img[:, 1:, :, :],
        )
        self.log("Val/loss", loss, on_epoch=True)

        return loss

    def test_step(self, batch, batch_idx):
        (source_img, _), (
            target_img,
            target_palette,
        ) = batch
        target_palette = nn.Flatten()(target_palette)
        recolored_img_ab = self.generator(source_img, target_palette)
        loss = self.MSE(
            recolored_img_ab,
            target_img[:, 1:, :, :],
        )
        self.log("Test/loss", loss, on_epoch=True)

        return loss

    def test_epoch_end(self, outputs):
        # log test loss
        loss_epoch = torch.stack(outputs).mean()
        self.log("Test/loss_epoch", loss_epoch)
        self.logger.log_hyperparams(self.hparams, loss_epoch)

    def configure_optimizers(self):
        # which is better? adam or adamw?
        if self.hparams.optimizer == "adam":
            optimizer = torch.optim.Adam(
                self.parameters(),
                lr=self.hparams.learning_rate,
                betas=(
                    self.hparams.beta_1,
                    self.hparams.beta_2,
                ),
                weight_decay=self.hparams.weight_decay,
            )
        elif self.hparams.optimizer == "adamw":
            optimizer = torch.optim.Adam(
                self.parameters(),
                lr=self.hparams.learning_rate,
                betas=(
                    self.hparams.beta_1,
                    self.hparams.beta_2,
                ),
                weight_decay=self.hparams.weight_decay,
            )
        else:
            raise NotImplementedError(f"Optimizer {self.hparams.optimizer} is not implemented")

        lr_scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer=optimizer,
            mode="min",
            patience=self.hparams.scheduler_patience,
        )

        return {
            "optimizer": optimizer,
            "lr_scheduler": lr_scheduler,
            "monitor": "Val/loss_epoch",
        }

    @property
    def example_input_array(self):
        (source_img, _), (
            target_img,
            target_palette,
        ) = next(iter(self.val_dataloader()))
        return source_img[0:1, ...], nn.Flatten()(target_palette[0:1, ...])


class AdversarialMSESystem(pl.LightningModule):
    """
    Wrapper for adversarial training of PaletteNet combined with MSE loss.
    """

    # TODO: refactor

    def __init__(
        self,
        generator=None,
        discriminator=None,
        lambda_mse_loss=DEFAULT_ADVERSARIAL_LAMBDA_MSE_LOSS,
        generator_learning_rate=DEFAULT_GENERATOR_LR,
        discriminator_learning_rate=DEFAULT_DISCRIMINATOR_LR,
        beta_1=DEFAULT_ADVERSARIAL_BETA_1,
        beta_2=DEFAULT_ADVERSARIAL_BETA_2,
        generator_weight_decay=DEFAULT_GENERATOR_WEIGHT_DECAY,
        discriminator_weight_decay=DEFAULT_DISCRIMINATOR_WEIGHT_DECAY,
        optimizer="adam",
        batch_size=8,
        multiplier=16,
        k=4,
        p=0.1,
    ):
        super().__init__()

        # manually configure hyperparams in case we add some non-hyperparams arguments in the future
        # they are saved to `self.hparams`
        self.save_hyperparameters(
            "lambda_mse_loss",
            "generator_learning_rate",
            "discriminator_learning_rate",
            "beta_1",
            "beta_2",
            "generator_weight_decay",
            "discriminator_weight_decay",
            "optimizer",
            "batch_size",
            "multiplier",
            "k",
            "p",
        )

        if generator is None:
            generator = PaletteNet()
        if discriminator is None:
            discriminator = Discriminator(p=p)

        self.generator = generator
        self.discriminator = discriminator
        self.k = k

        self.MSE = torch.nn.MSELoss()
        self.normalizer = LABNormalizer()

    def forward(self, img, palette):
        return self.generator(img, palette)

    def training_step(self, batch, batch_idx, optimizer_idx):
        (
            (source_img, _),
            (target_img, target_palette),
            (original_img, original_palette),
        ) = batch
        target_palette = nn.Flatten()(target_palette)
        original_palette = nn.Flatten()(original_palette)
        luminance = source_img[:, 0:1, :, :]
        recolored_img_ab = self.generator(source_img, target_palette)
        recolored_img = torch.cat([luminance, recolored_img_ab], dim=-3)
        mse_loss = self.MSE(
            recolored_img_ab,
            target_img[:, 1:, :, :],
        )
        # self.log("Train/mse_loss_step", mse_loss)
        self.log(
            "Train/mse_loss",
            mse_loss,
            on_epoch=True,
        )

        # train generator
        if optimizer_idx == 0:
            real_prob_tt = self.discriminator(
                recolored_img,
                target_palette,
            )
            adv_loss = -torch.mean(torch.log(real_prob_tt))
            generator_loss = mse_loss * self.hparams.lambda_mse_loss + adv_loss
            self.log(
                "Train/adv_loss",
                adv_loss,
                on_epoch=True,
            )
            self.log(
                "Train/generator_loss",
                generator_loss,
                on_epoch=True,
            )

            return generator_loss

        # train discriminator
        elif optimizer_idx == 1:
            # add noise
            noise_amplitude = 0.1 / ((batch_idx + 1) ** (1 / 4))
            recolored_img += torch.normal(
                mean=0, std=noise_amplitude, size=recolored_img.shape, device=recolored_img.device
            )
            original_img += torch.normal(
                mean=0, std=noise_amplitude, size=original_img.shape, device=original_img.device
            )

            fake_prob_tt = 1.0 - self.discriminator(
                recolored_img.detach(),
                target_palette,
            )
            fake_prob_to = 1.0 - self.discriminator(
                recolored_img,
                original_palette,
            )
            fake_prob_ot = 1.0 - self.discriminator(original_img, target_palette)
            real_prob_oo = self.discriminator(original_img, original_palette)

            # discriminator_tt = -torch.mean(torch.log(fake_prob_tt))
            # discriminator_to = -torch.mean(torch.log(fake_prob_to))
            # discriminator_ot = -torch.mean(torch.log(fake_prob_ot))
            # discriminator_oo = -torch.mean(torch.log(real_prob_oo))
            # discriminator_loss = (
            #         discriminator_tt + discriminator_to + discriminator_ot + discriminator_oo
            # )
            # self.log(
            #     "Train/discriminator_tt",
            #     discriminator_tt,
            #     on_epoch=True,
            # )
            # self.log(
            #     "Train/discriminator_to",
            #     discriminator_to,
            #     on_epoch=True,
            # )
            # self.log(
            #     "Train/discriminator_ot",
            #     discriminator_ot,
            #     on_epoch=True,
            # )
            # self.log(
            #     "Train/discriminator_oo",
            #     discriminator_oo,
            #     on_epoch=True,
            # )
            # self.log(
            #     "Train/discriminator_loss",
            #     discriminator_loss,
            #     on_epoch=True,
            # )

            discriminator_loss = -(
                torch.mean(torch.log(fake_prob_tt))
                + torch.mean(torch.log(fake_prob_to))
                + torch.mean(torch.log(fake_prob_ot))
                + torch.mean(torch.log(real_prob_oo))
            )

            return discriminator_loss
        else:
            raise ValueError(f"Wrong optimizer index: {optimizer_idx}")

    def validation_step(self, batch, batch_idx):
        (
            (source_img, _),
            (target_img, target_palette),
            (original_img, original_palette),
        ) = batch

        target_palette = nn.Flatten()(target_palette)
        original_palette = nn.Flatten()(original_palette)
        luminance = source_img[:, 0:1, :, :]
        recolored_img_ab = self.generator(source_img, target_palette)
        recolored_img = torch.cat([luminance, recolored_img_ab], dim=-3)

        mse_loss = self.MSE(
            recolored_img_ab,
            target_img[:, 1:, :, :],
        )

        # generator loss
        real_prob_tt = self.discriminator(recolored_img.detach(), target_palette)
        adv_loss = -torch.mean(torch.log(real_prob_tt))
        generator_loss = mse_loss * self.hparams.lambda_mse_loss + adv_loss

        # discriminator loss
        fake_prob_tt = 1.0 - self.discriminator(recolored_img.detach(), target_palette)
        fake_prob_to = 1.0 - self.discriminator(recolored_img.detach(), original_palette)
        fake_prob_ot = 1.0 - self.discriminator(original_img, target_palette)
        real_prob_oo = self.discriminator(original_img, original_palette)

        # discriminator_tt = -torch.mean(torch.log(fake_prob_tt))
        # discriminator_to = -torch.mean(torch.log(fake_prob_to))
        # discriminator_ot = -torch.mean(torch.log(fake_prob_ot))
        # discriminator_oo = -torch.mean(torch.log(real_prob_oo))
        # discriminator_loss = (
        #         discriminator_tt + discriminator_to + discriminator_ot + discriminator_oo
        # )
        # self.log(
        #     "Train/discriminator_tt",
        #     discriminator_tt,
        #     on_epoch=True,
        # )
        # self.log(
        #     "Train/discriminator_to",
        #     discriminator_to,
        #     on_epoch=True,
        # )
        # self.log(
        #     "Train/discriminator_ot",
        #     discriminator_ot,
        #     on_epoch=True,
        # )
        # self.log(
        #     "Train/discriminator_oo",
        #     discriminator_oo,
        #     on_epoch=True,
        # )
        discriminator_loss = -(
            torch.mean(torch.log(fake_prob_tt))
            + torch.mean(torch.log(fake_prob_to))
            + torch.mean(torch.log(fake_prob_ot))
            + torch.mean(torch.log(real_prob_oo))
        )

        self.log(
            "Val/adv_loss",
            adv_loss,
            on_epoch=True,
        )
        self.log(
            "Val/mse_loss_epoch",
            mse_loss,
            on_epoch=True,
        )
        self.log(
            "Val/generator_loss",
            generator_loss,
            on_epoch=True,
        )
        self.log(
            "Val/discriminator_loss",
            discriminator_loss,
            on_epoch=True,
        )

    def test_step(self, batch, batch_idx):
        (
            (source_img, _),
            (target_img, target_palette),
            (original_img, original_palette),
        ) = batch

        target_palette = nn.Flatten()(target_palette)
        original_palette = nn.Flatten()(original_palette)
        luminance = source_img[:, 0:1, :, :]
        recolored_img_ab = self.generator(source_img, target_palette)
        recolored_img = torch.cat([luminance, recolored_img_ab], dim=-3)

        mse_loss = self.MSE(
            recolored_img_ab,
            target_img[:, 1:, :, :],
        )

        # generator loss
        real_prob_tt = self.discriminator(recolored_img.detach(), target_palette)
        adv_loss = -torch.mean(torch.log(real_prob_tt))
        generator_loss = mse_loss * self.hparams.lambda_mse_loss + adv_loss

        # discriminator loss
        fake_prob_tt = 1.0 - self.discriminator(recolored_img.detach(), target_palette)
        fake_prob_to = 1.0 - self.discriminator(recolored_img.detach(), original_palette)
        fake_prob_ot = 1.0 - self.discriminator(original_img, target_palette)
        real_prob_oo = self.discriminator(original_img, original_palette)

        # discriminator_tt = -torch.mean(torch.log(fake_prob_tt))
        # discriminator_to = -torch.mean(torch.log(fake_prob_to))
        # discriminator_ot = -torch.mean(torch.log(fake_prob_ot))
        # discriminator_oo = -torch.mean(torch.log(real_prob_oo))
        # discriminator_loss = (
        #         discriminator_tt + discriminator_to + discriminator_ot + discriminator_oo
        # )
        # self.log(
        #     "Train/discriminator_tt",
        #     discriminator_tt,
        #     on_epoch=True,
        # )
        # self.log(
        #     "Train/discriminator_to",
        #     discriminator_to,
        #     on_epoch=True,
        # )
        # self.log(
        #     "Train/discriminator_ot",
        #     discriminator_ot,
        #     on_epoch=True,
        # )
        # self.log(
        #     "Train/discriminator_oo",
        #     discriminator_oo,
        #     on_epoch=True,
        # )

        discriminator_loss = -(
            torch.mean(torch.log(fake_prob_tt))
            + torch.mean(torch.log(fake_prob_to))
            + torch.mean(torch.log(fake_prob_ot))
            + torch.mean(torch.log(real_prob_oo))
        )

        self.log(
            "Test/adv_loss",
            adv_loss,
            on_epoch=True,
        )
        self.log(
            "Test/mse_loss",
            mse_loss,
            on_epoch=True,
        )
        self.log(
            "Test/generator_loss",
            generator_loss,
            on_epoch=True,
        )
        self.log(
            "Test/discriminator_loss",
            discriminator_loss,
            on_epoch=True,
        )

    def configure_optimizers(self):
        if self.hparams.optimizer == "adam":
            optimizer_generator = torch.optim.Adam(
                self.generator.recoloring_decoder.parameters(),
                lr=self.hparams.generator_learning_rate,
                betas=(
                    self.hparams.beta_1,
                    self.hparams.beta_2,
                ),
                weight_decay=self.hparams.generator_weight_decay,
            )
            optimizer_discriminator = torch.optim.Adam(
                self.discriminator.parameters(),
                lr=self.hparams.discriminator_learning_rate,
                betas=(
                    self.hparams.beta_1,
                    self.hparams.beta_2,
                ),
                weight_decay=self.hparams.discriminator_weight_decay,
            )

        elif self.hparams.optimizer == "adamw":
            optimizer_generator = torch.optim.AdamW(
                self.generator.recoloring_decoder.parameters(),
                lr=self.hparams.generator_learning_rate,
                betas=(
                    self.hparams.beta_1,
                    self.hparams.beta_2,
                ),
                weight_decay=self.hparams.generator_weight_decay,
            )
            optimizer_discriminator = torch.optim.AdamW(
                self.discriminator.parameters(),
                lr=self.hparams.discriminator_learning_rate,
                betas=(
                    self.hparams.beta_1,
                    self.hparams.beta_2,
                ),
                weight_decay=self.hparams.discriminator_weight_decay,
            )
        else:
            raise NotImplementedError(f"Optimizer {self.hparams.optimizer} is not implemented")

        optimizers = [
            optimizer_generator,
            optimizer_discriminator,
        ]
        # schedulers = [
        #     {"scheduler": lr_scheduler_generator, "monitor": "Val/generator_loss_epoch", "interval": "epoch"},
        #     {"scheduler": lr_scheduler_discriminator, "monitor": "Val/discriminator_loss_epoch", "interval": "epoch"}
        # ]

        return optimizers

    # Alternating schedule for optimizer steps (ie: GANs)
    def optimizer_step(
        self,
        current_epoch,
        batch_nb,
        optimizer,
        optimizer_idx,
        closure,
        on_tpu=False,
        using_native_amp=False,
        using_lbfgs=False,
    ):
        # update generator opt every step
        if optimizer_idx == 0:
            optimizer.step(closure=closure)

        # update discriminator opt every k steps
        if optimizer_idx == 1:
            if batch_nb % self.k == 0:
                optimizer.step(closure=closure)


class AdversarialSystem(pl.LightningModule):
    """
    Wrapper for adversarial training of PaletteNet.
    """

    # TODO: refactor

    def __init__(
        self,
        generator=None,
        discriminator=None,
        lambda_mse_loss=DEFAULT_ADVERSARIAL_LAMBDA_MSE_LOSS,
        generator_learning_rate=DEFAULT_GENERATOR_LR,
        discriminator_learning_rate=DEFAULT_DISCRIMINATOR_LR,
        beta_1=DEFAULT_ADVERSARIAL_BETA_1,
        beta_2=DEFAULT_ADVERSARIAL_BETA_2,
        generator_weight_decay=DEFAULT_GENERATOR_WEIGHT_DECAY,
        discriminator_weight_decay=DEFAULT_DISCRIMINATOR_WEIGHT_DECAY,
        optimizer="adam",
        batch_size=8,
        multiplier=16,
        k=4,
        p=0.1,
    ):
        super().__init__()

        # manually configure hyperparams in case we add some non-hyperparams arguments in the future
        # they are saved to `self.hparams`
        self.save_hyperparameters(
            "lambda_mse_loss",
            "generator_learning_rate",
            "discriminator_learning_rate",
            "beta_1",
            "beta_2",
            "generator_weight_decay",
            "discriminator_weight_decay",
            "optimizer",
            "batch_size",
            "multiplier",
            "k",
            "p",
        )

        if generator is None:
            generator = PaletteNet()
        if discriminator is None:
            discriminator = Discriminator(p=p)

        self.generator = generator
        self.discriminator = discriminator
        self.k = k

        self.MSE = torch.nn.MSELoss()
        self.normalizer = LABNormalizer()

    def forward(self, img, palette):
        return self.generator(img, palette)

    def training_step(self, batch, batch_idx, optimizer_idx):
        (source_image, source_palette), (original_image, target_palette) = batch
        target_palette = nn.Flatten()(target_palette)
        source_palette = nn.Flatten()(source_palette)
        luminance = source_image[:, 0:1, :, :]
        recolored_img_ab = self.generator(source_image, target_palette)
        recolored_img = torch.cat([luminance, recolored_img_ab], dim=-3)

        # train generator
        if optimizer_idx == 0:
            real_prob_tt = self.discriminator(recolored_img, target_palette)
            adv_loss = -torch.mean(torch.log(real_prob_tt))
            generator_loss = adv_loss
            self.log(
                "Train/adv_loss",
                adv_loss,
                on_epoch=True,
            )
            self.log(
                "Train/generator_loss",
                generator_loss,
                on_epoch=True,
            )

            return generator_loss

        # train discriminator
        elif optimizer_idx == 1:
            # add noise
            noise_amplitude = 0.1 / ((batch_idx + 1) ** (1 / 4))
            recolored_img += torch.normal(
                mean=0, std=noise_amplitude, size=recolored_img.shape, device=recolored_img.device
            )
            source_image += torch.normal(
                mean=0, std=noise_amplitude, size=source_image.shape, device=source_image.device
            )

            fake_prob_tt = 1.0 - self.discriminator(
                recolored_img.detach(),
                target_palette,
            )
            fake_prob_to = 1.0 - self.discriminator(
                recolored_img.detach(),
                source_palette,
            )
            fake_prob_ot = 1.0 - self.discriminator(source_image, target_palette)
            real_prob_oo = self.discriminator(source_image, source_palette)

            # discriminator_tt = -torch.mean(torch.log(fake_prob_tt))
            # discriminator_to = -torch.mean(torch.log(fake_prob_to))
            # discriminator_ot = -torch.mean(torch.log(fake_prob_ot))
            # discriminator_oo = -torch.mean(torch.log(real_prob_oo))
            # discriminator_loss = (
            #     discriminator_tt + discriminator_to + discriminator_ot + discriminator_oo
            # )
            # self.log(
            #     "Train/discriminator_tt",
            #     discriminator_tt,
            #     on_epoch=True,
            # )
            # self.log(
            #     "Train/discriminator_to",
            #     discriminator_to,
            #     on_epoch=True,
            # )
            # self.log(
            #     "Train/discriminator_ot",
            #     discriminator_ot,
            #     on_epoch=True,
            # )
            # self.log(
            #     "Train/discriminator_oo",
            #     discriminator_oo,
            #     on_epoch=True,
            # )

            discriminator_loss = -(
                torch.mean(torch.log(fake_prob_tt))
                + torch.mean(torch.log(fake_prob_to))
                + torch.mean(torch.log(fake_prob_ot))
                + torch.mean(torch.log(real_prob_oo))
            )

            self.log(
                "Train/discriminator_loss",
                discriminator_loss,
                on_epoch=True,
            )

            return discriminator_loss
        else:
            raise ValueError(f"Wrong optimizer index: {optimizer_idx}")

    def validation_step(self, batch, batch_idx):
        (source_image, source_palette), (original_image, target_palette) = batch
        target_palette = nn.Flatten()(target_palette)
        source_palette = nn.Flatten()(source_palette)
        luminance = source_image[:, 0:1, :, :]
        recolored_img_ab = self.generator(source_image, target_palette)
        recolored_img = torch.cat([luminance, recolored_img_ab], dim=-3)

        # generator loss
        real_prob_tt = self.discriminator(recolored_img.detach(), target_palette)
        adv_loss = -torch.mean(torch.log(real_prob_tt))
        generator_loss = adv_loss

        # discriminator loss
        fake_prob_tt = 1.0 - self.discriminator(
            recolored_img.detach(),
            target_palette,
        )
        fake_prob_to = 1.0 - self.discriminator(
            recolored_img.detach(),
            source_palette,
        )
        fake_prob_ot = 1.0 - self.discriminator(source_image, target_palette)
        real_prob_oo = self.discriminator(source_image, source_palette)
        # discriminator_tt = -torch.mean(torch.log(fake_prob_tt))
        # discriminator_to = -torch.mean(torch.log(fake_prob_to))
        # discriminator_ot = -torch.mean(torch.log(fake_prob_ot))
        # discriminator_oo = -torch.mean(torch.log(real_prob_oo))
        # discriminator_loss = (
        #         discriminator_tt + discriminator_to + discriminator_ot + discriminator_oo
        # )

        discriminator_loss = -(
            torch.mean(torch.log(fake_prob_tt))
            + torch.mean(torch.log(fake_prob_to))
            + torch.mean(torch.log(fake_prob_ot))
            + torch.mean(torch.log(real_prob_oo))
        )

        self.log(
            "Val/adv_loss",
            adv_loss,
            on_epoch=True,
        )
        self.log(
            "Val/generator_loss",
            generator_loss,
            on_epoch=True,
        )
        self.log(
            "Val/discriminator_loss",
            discriminator_loss,
            on_epoch=True,
        )

    def test_step(self, batch, batch_idx):
        (source_image, source_palette), (original_image, target_palette) = batch
        target_palette = nn.Flatten()(target_palette)
        source_palette = nn.Flatten()(source_palette)
        luminance = source_image[:, 0:1, :, :]
        recolored_img_ab = self.generator(source_image, target_palette)
        recolored_img = torch.cat([luminance, recolored_img_ab], dim=-3)

        # generator loss
        real_prob_tt = self.discriminator(recolored_img.detach(), target_palette)
        adv_loss = -torch.mean(torch.log(real_prob_tt))
        generator_loss = adv_loss

        # discriminator loss
        fake_prob_tt = 1.0 - self.discriminator(
            recolored_img.detach(),
            target_palette,
        )
        fake_prob_to = 1.0 - self.discriminator(
            recolored_img.detach(),
            source_palette,
        )
        fake_prob_ot = 1.0 - self.discriminator(source_image, target_palette)
        real_prob_oo = self.discriminator(source_image, source_palette)
        # discriminator_tt = -torch.mean(torch.log(fake_prob_tt))
        # discriminator_to = -torch.mean(torch.log(fake_prob_to))
        # discriminator_ot = -torch.mean(torch.log(fake_prob_ot))
        # discriminator_oo = -torch.mean(torch.log(real_prob_oo))
        # discriminator_loss = (
        #         discriminator_tt + discriminator_to + discriminator_ot + discriminator_oo
        # )

        discriminator_loss = -(
            torch.mean(torch.log(fake_prob_tt))
            + torch.mean(torch.log(fake_prob_to))
            + torch.mean(torch.log(fake_prob_ot))
            + torch.mean(torch.log(real_prob_oo))
        )

        self.log(
            "Test/adv_loss",
            adv_loss,
            on_epoch=True,
        )
        self.log(
            "Test/generator_loss",
            generator_loss,
            on_epoch=True,
        )
        self.log(
            "Test/discriminator_loss",
            discriminator_loss,
            on_epoch=True,
        )

    def configure_optimizers(self):
        if self.hparams.optimizer == "adam":
            optimizer_generator = torch.optim.Adam(
                self.generator.recoloring_decoder.parameters(),
                lr=self.hparams.generator_learning_rate,
                betas=(
                    self.hparams.beta_1,
                    self.hparams.beta_2,
                ),
                weight_decay=self.hparams.generator_weight_decay,
            )
            optimizer_discriminator = torch.optim.Adam(
                self.discriminator.parameters(),
                lr=self.hparams.discriminator_learning_rate,
                betas=(
                    self.hparams.beta_1,
                    self.hparams.beta_2,
                ),
                weight_decay=self.hparams.discriminator_weight_decay,
            )

        elif self.hparams.optimizer == "adamw":
            optimizer_generator = torch.optim.AdamW(
                self.generator.recoloring_decoder.parameters(),
                lr=self.hparams.generator_learning_rate,
                betas=(
                    self.hparams.beta_1,
                    self.hparams.beta_2,
                ),
                weight_decay=self.hparams.generator_weight_decay,
            )
            optimizer_discriminator = torch.optim.AdamW(
                self.discriminator.parameters(),
                lr=self.hparams.discriminator_learning_rate,
                betas=(
                    self.hparams.beta_1,
                    self.hparams.beta_2,
                ),
                weight_decay=self.hparams.discriminator_weight_decay,
            )
        else:
            raise NotImplementedError(f"Optimizer {self.hparams.optimizer} is not implemented")

        optimizers = [
            optimizer_generator,
            optimizer_discriminator,
        ]
        # schedulers = [
        #     {"scheduler": lr_scheduler_generator, "monitor": "Val/generator_loss_epoch", "interval": "epoch"},
        #     {"scheduler": lr_scheduler_discriminator, "monitor": "Val/discriminator_loss_epoch", "interval": "epoch"}
        # ]

        return optimizers

    # Alternating schedule for optimizer steps (ie: GANs)
    def optimizer_step(
        self,
        current_epoch,
        batch_nb,
        optimizer,
        optimizer_idx,
        closure,
        on_tpu=False,
        using_native_amp=False,
        using_lbfgs=False,
    ):
        # update generator opt every step
        if optimizer_idx == 0:
            optimizer.step(closure=closure)

        # update discriminator opt every k steps
        if optimizer_idx == 1:
            if batch_nb % self.k == 0:
                optimizer.step(closure=closure)
