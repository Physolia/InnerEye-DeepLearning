#  ------------------------------------------------------------------------------------------
#  Copyright (c) Microsoft Corporation. All rights reserved.
#  Licensed under the MIT License (MIT). See LICENSE in the repo root for license information.
#  ------------------------------------------------------------------------------------------

from pathlib import Path
import sys
import os
from torch.utils.data import DataLoader
from datetime import datetime
from pytorch_lightning import seed_everything, Trainer
from pytorch_lightning.callbacks.model_checkpoint import ModelCheckpoint
from argparse import ArgumentParser
current_file = Path(__file__)
root_dir = current_file.parent.parent.parent
sys.path.append(str(root_dir / 'stylegan2-ada-pytorch'))
sys.path.append(str(root_dir))
sys.path.append(str(root_dir / 'InnerEye-Generative'))
from health.azure.himl import submit_to_azure_if_needed
from loaders.prostate_loader import Prostate2DSimpleDataset
from loaders.gen_imgs_and_seg_loader import SynthDataset
from models.UNet2D_seg_baseline import UNet_multi_val
from helpers.loggers import AzureMLLogger, TensorboardWithImgs, LoggerCollectionWithImgs
from locations import DATASETPATH, ENVIRONMNENT, WORKSPACECONFIG, CLUSTER, DATASTORE, GENSCANSPATH
DATASETPATH = Path(DATASETPATH)
ENVIRONMNENT = Path(ENVIRONMNENT)
WORKSPACECONFIG = Path(WORKSPACECONFIG)
GENSCANSPATH = Path(GENSCANSPATH)

if __name__ == '__main__':
    print('{} -- {}:{}'.format(datetime.now().date(), datetime.now().hour+1, datetime.now().minute), end=' ')
    print('-- Starting up')
    seed_everything(1234)
    # args
    parser = ArgumentParser()
    parser.add_argument("--batch_size", default=32, type=int)
    parser.add_argument("--dataset_path", default='', type=str)
    parser.add_argument("--csv_base_name", default='dataset.csv', type=str)  
    parser.add_argument("--debug", default=False, action='store_true')
    parser.add_argument("--gpu", default=None, type=int)
    parser.add_argument("--azureml", '-aml', action='store_true', default=False, help="submit to azureml")
    parser.add_argument("--segStyleGAN_folder", '-r', default='n_16', type=str)
    
    parser = Trainer.add_argparse_args(parser)
    parser = UNet_multi_val.add_model_specific_args(parser)

    args = parser.parse_args()

    callbacks = [] 

    if args.azureml:
        run_info = submit_to_azure_if_needed(entry_script=current_file,
                                            snapshot_root_directory=root_dir,
                                            workspace_config_file=WORKSPACECONFIG,
                                            compute_cluster_name=CLUSTER,
                                            default_datastore=DATASTORE,
                                            conda_environment_file=ENVIRONMNENT,
                                            input_datasets=["", ""],
                                            submit_to_azureml=args.azureml
                                             )
    # run on indicated GPU:
    if args.gpu is not None and isinstance(args.gpu, int):
        # Make sure that it only uses a single GPU..
        os.environ['CUDA_VISIBLE_DEVICES'] = str(args.gpu)
        args.gpus = 1

    # initialise model
    model = UNet_multi_val(**vars(args))

    # initialise loaders
    real_val_ds = Prostate2DSimpleDataset(run_info.input_datasets[1], args.csv_base_name, 'val', 
                                           1, labels=['femurs', 'bladder', 'prostate'])
    train_ds = SynthDataset(run_info.input_datasets[0], 'train', args.debug)
    synth_val_ds = SynthDataset(run_info.input_datasets[0], 'val', args.debug)
    train_dl = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True, num_workers=12)
    synth_val_dl = DataLoader(synth_val_ds, batch_size=args.batch_size, num_workers=12)
    real_val_dl = DataLoader(real_val_ds, batch_size=args.batch_size, num_workers=12)

    logger = TensorboardWithImgs('./outputs/UNet')
    callbacks.append(ModelCheckpoint(dirpath='./outputs/UNet/{}/version_{}/checkpoints'.format(logger.name, logger.version), 
                                     monitor='mean_DICE/test/dataloader_idx_0', mode='max', save_top_k=3, auto_insert_metric_name=False,
                                     filename='epoch={epoch}-step={step}-val_loss={val_loss:.2f}-mean_DICE_val={mean_DICE/test:.2f}'))
    if args.azureml:
        AMLlogger = AzureMLLogger()
        logger = LoggerCollectionWithImgs([logger, AMLlogger])

    if args.debug:
        trainer = Trainer.from_argparse_args(args, progress_bar_refresh_rate=20, fast_dev_run=True, logger=logger)
        trainer.fit(model, train_dl, [synth_val_dl, real_val_dl])

    trainer = Trainer.from_argparse_args(args, progress_bar_refresh_rate=20, 
                                         fast_dev_run=False, logger=logger, 
                                         checkpoint_callback=True, 
                                         callbacks=callbacks if len(callbacks)>0 else None)
    trainer.fit(model, train_dl, [synth_val_dl, real_val_dl])