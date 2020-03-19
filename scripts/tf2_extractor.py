"""This script requires TensorFlow 2.x and a SavedModel export of obj-det
models."""

import os
import sys
import time
from pathlib import Path
from multiprocessing import Pool
import argparse
import gzip
import pickle


# Disable thread explosion
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['OMP_NUM_THREADS'] = '1'
os.environ['MKL_NUM_THREADS'] = '1'

import tensorflow as tf
tf.config.threading.set_inter_op_parallelism_threads(2)
tf.config.threading.set_intra_op_parallelism_threads(2)

import numpy as np
import tqdm

try:
    import hickle
    HICKLE = True
except ImportError as _:
    HICKLE = False


OUT_FIELDS = [
    'detection_scores',
    'detection_classes',
    'detection_boxes',
    'detection_features',
]


def prepare_dict(output, class_offset=0, num_proposals=10000):
    d = {k: output[k].numpy().squeeze(0)[:num_proposals] for k in OUT_FIELDS}
    d['num_detections'] = min(int(output['num_detections']), num_proposals)
    d['detection_classes'] = (d['detection_classes'] - float(class_offset)).astype(np.uint16)
    return d


def read_image_list(fname):
    fnames = []
    with open(fname) as f:
        for line in f:
            fnames.append(line.strip())
    return fnames


if __name__ == '__main__':
    parser = argparse.ArgumentParser(prog='tfobj-extractor')
    parser.add_argument('-m', '--model-folder', type=str,
                        default="/data/ozan/tools/tfobj-models/faster_rcnn_inception_resnet_v2_atrous_oid_v4_2018_12_12_ghconfig_reexport",
                        help='Model folder where checkpoints reside.')
    parser.add_argument('-l', '--list-of-images', type=str, required=True,
                        help='list of image locations for which features will be extracted.')
    parser.add_argument('-i', '--img-root', default='/data/ozan/datasets/conceptual_captions/raw_files/images',
                        help='Image root to be prepended to every image name.')
    parser.add_argument('-o', '--output-folder', type=str, required=True,
                        help='Output folder where features will be saved.')
    parser.add_argument('-f', '--format', default='pickle',
                        help='Output file format.')
    parser.add_argument('-p', '--parallel', action='store_true',
                        help='Parallel dumper process for output files.')
    parser.add_argument('-n', '--num-proposals', type=int, default=36,
                        help='Number of top proposals to accept.')
    parser.add_argument('-O', '--img-offset', default=0, type=int,
                        help='Subtract this value before saving the files.')

    args = parser.parse_args()

    # Setup compressor
    assert args.format in ("pickle", "hickle", "npz"), "Output file format unknown."

    if args.format == 'hickle' and not HICKLE:
        print('Please install hickle i.e. `pip install hickle`')
        sys.exit(1)

    if 'oid_v4' in args.model_folder:
        num_classes = 601
    else:
        print('Only oid_v4 models are supported so far')
        sys.exit(1)

    def get_dump_fn():
        def _pickle(feat_dict, fname):
            with gzip.GzipFile(fname, 'wb', compresslevel=2) as f:
                pickle.dump(feat_dict, f, protocol=4, fix_imports=False)
        def _hickle(feat_dict, fname):
            hickle.dump(feat_dict, fname, 'w', compression='lzf')
        def _npz(feat_dict, fname):
            np.savez_compressed(fname, **feat_dict)

        if args.format == 'pickle':
            return _pickle, '.pkl.gz'
        elif args.format == 'hickle':
            return _hickle, '.hkl'
        elif args.format == 'npz':
            return _npz, '.npz'

    dump_detections, dump_suffix = get_dump_fn()

    if args.parallel:
        pool = Pool(processes=2)
    else:
        pool = None

    out_folder = Path(args.output_folder) / Path(args.model_folder).name
    out_folder.mkdir(exist_ok=True, parents=True)

    pre_extraction = time.time()
    # Load image list
    image_list = read_image_list(args.list_of_images)
    if '/' not in image_list[0]:
        root = Path(args.img_root)
        image_list = [str(root / img) for img in image_list]

    print(f'Will extract features for (at most) {len(image_list)} images.')
    ds = tf.data.Dataset.from_tensor_slices(image_list)
    dataset = ds.map(
        lambda x: tf.image.decode_image(
            tf.io.read_file(x), expand_animations=False, channels=3),
            num_parallel_calls=2).prefetch(4)

    # build the model
    model = tf.saved_model.load(
        str(Path(args.model_folder) / 'saved_model')).signatures['serving_default']

    # Introspect num_classes to compute the class_offset for the bg-class
    class_offset = model.structured_outputs['raw_detection_scores'].shape[-1] - num_classes
    print('Class offset is: ', class_offset)

    print('Warming up model')
    model(tf.convert_to_tensor(np.ones((1, 300, 300, 3), dtype=np.uint8)))
    pre_extraction = time.time() - pre_extraction
    print(f'Setup took {pre_extraction:.3f} seconds')

    ##########
    # mainloop
    ##########
    problems = {}
    n_total = len(image_list)
    n_extracted = 0
    for idx, img in enumerate(tqdm.tqdm(dataset, total=len(image_list))):
        orig_img_name = image_list[idx].split('/')[-1]
        # to be compatible with Hacettepe file naming (0-indexed)
        dump_img_name = str(int(orig_img_name) - args.img_offset)
        dump_fname = str(out_folder / dump_img_name) + dump_suffix

        if img.shape.num_elements() < 100*100*3:
            # image too small
            problems[orig_img_name] = 'too small'
        elif not os.path.exists(dump_fname):
            try:
                dets = model(img[None, ...])
            except Exception as _:
                problems[orig_img_name] = 'inference exception'
            else:
                breakpoint()
                dets = prepare_dict(
                    dets, class_offset=class_offset, num_proposals=args.num_proposals)
                n_extracted += 1
                if pool:
                    pool.apply_async(dump_detections, (dets, dump_fname))
                else:
                    dump_detections(dets, dump_fname)

    if len(problems) > 0:
        fname = str(out_folder).rstrip('/') + '.txt'
        with open(fname, 'w') as f:
            for img_name, prob in problems.items():
                f.write(f'{img_name}\t{prob}')

    print()
    print(f'# of total images requested: {n_total}')
    print(f'# of rejects/problems: {len(problems)}')
    print(f'# of newly extracted features: {n_extracted}')
