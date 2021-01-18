import argparse
from pytorch_lightning import Trainer
from pytorch_lightning.callbacks import GPUStatsMonitor, ModelCheckpoint
from dotenv import load_dotenv
import os
from uuid import uuid1


from repalette.constants import (
    S3_LIGHTNING_LOGS_DIR,
    S3_MODEL_CHECKPOINTS_RELATIVE_DIR,
    MODEL_CHECKPOINTS_DIR,
    LIGHTNING_LOGS_DIR,
    PRETRAINED_MODEL_CHECKPOINT_PATH,
)
from pytorch_lightning.loggers import TensorBoardLogger
from repalette.lightning.datamodules import AdversarialRecolorDataModule
from repalette.lightning.callbacks import (
    LogAdversarialMSEToTensorboard,
    Notify,
    LogHyperparamsToTensorboard,
)
from repalette.lightning.systems import PreTrainSystem, AdversarialMSESystem


def main(hparams):
    if hparams.checkpoints_location == "s3":
        checkpoints_dir = os.path.join(
            S3_MODEL_CHECKPOINTS_RELATIVE_DIR, hparams.name, hparams.version
        )
    else:
        checkpoints_dir = os.path.join(MODEL_CHECKPOINTS_DIR, hparams.name, hparams.version)

    if hparams.logging_location == "s3":
        logging_dir = os.path.join(S3_LIGHTNING_LOGS_DIR, hparams.name)
    else:
        logging_dir = os.path.join(LIGHTNING_LOGS_DIR, hparams.name)

    # load generator pretrained with PreTrainSystem
    generator = PreTrainSystem.load_from_checkpoint(PRETRAINED_MODEL_CHECKPOINT_PATH).generator

    # main LightningModule
    adversarial_system = AdversarialMSESystem(generator=generator, **vars(hparams))

    adversarial_checkpoints = ModelCheckpoint(
        dirpath=checkpoints_dir,
        monitor="Val/adv_loss",
        verbose=True,
        mode="min",
        save_top_k=-1,
    )

    gpu_stats = GPUStatsMonitor(temperature=True)

    log_recolored_to_tensorboard = LogAdversarialMSEToTensorboard()
    log_hyperparams_to_tensorboard = LogHyperparamsToTensorboard(hp_metric=None)

    notify = Notify()

    logger = TensorBoardLogger(
        logging_dir,
        name=hparams.name,
        version=hparams.version,
        log_graph=True,
        default_hp_metric=False,
    )

    trainer = Trainer.from_argparse_args(
        hparams,
        logger=logger,
        checkpoint_callback=adversarial_checkpoints,
        callbacks=[
            log_recolored_to_tensorboard,
            log_hyperparams_to_tensorboard,
            gpu_stats,
            notify,
        ],
        profiler="simple",
        benchmark=True,
        enable_pl_optimizer=True,
    )

    datamodule = AdversarialRecolorDataModule(**vars(hparams))

    trainer.fit(adversarial_system, datamodule=datamodule)

    # lightning automatically uses the best model checkpoint for testing
    trainer.test(adversarial_system, datamodule=datamodule)


if __name__ == "__main__":
    # load .env variables
    load_dotenv()

    # hyperparameters
    hparams_parser = argparse.ArgumentParser()

    # trainer
    hparams_parser.add_argument("--max-epochs", type=int, default=100)
    hparams_parser.add_argument("--gpus", type=int, default=-1)
    hparams_parser.add_argument("--precision", type=int, default=32, choices=[16, 32])
    hparams_parser.add_argument("--accumulate-grad-batches", type=int, default=1)
    hparams_parser.add_argument("--gradient-clip-val", type=float, default=0.0)
    hparams_parser.add_argument("--fast-dev-run", type=int, default=0)
    hparams_parser.add_argument("--track-grad-norm", type=int, default=-1)

    # callbacks
    hparams_parser.add_argument("--patience", type=int, default=10)

    # pretrain system
    hparams_parser = AdversarialMSESystem.add_argparse_args(hparams_parser)

    # datamodule
    hparams_parser = AdversarialRecolorDataModule.add_argparse_args(hparams_parser)

    # misc
    hparams_parser.add_argument(
        "--checkpoints-location", type=str, default="s3", choices=["s3", "local"]
    )
    hparams_parser.add_argument(
        "--logging-location", type=str, default="s3", choices=["s3", "local"]
    )
    hparams_parser.add_argument("--name", type=str, default="adversarial", help="experiment name")
    hparams_parser.add_argument(
        "--version",
        type=str,
        default=None,
        help="unique! run version - used to generate checkpoint S3 path",
    )

    hparams = hparams_parser.parse_args()

    if hparams.version is None:
        hparams.version = str(uuid1())

    main(hparams)
