# coding=utf-8
# Copyright 2020 The Trax Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Lint as: python3
"""Tests for trax.supervised.inputs."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import collections
import os

import gin
import numpy as np
from t5.data import preprocessors as t5_processors
import tensorflow as tf
import tensorflow_datasets as tfds
from trax.supervised import inputs

pkg_dir, _ = os.path.split(__file__)
_TESTDATA = os.path.join(pkg_dir, 'testdata')


def _test_dataset_ints(lengths):
  """Create a test dataset of int64 tensors of shape [length]."""
  def generator():
    """Sample generator of sequences of shape [length] of type int64."""
    for length in lengths:
      x = np.zeros([length], dtype=np.int64)
      yield (x, x)  # Inputs and targets are the same here.
  types = (tf.int64, tf.int64)
  shapes = (tf.TensorShape([None]), tf.TensorShape([None]))
  return tf.data.Dataset.from_generator(
      generator, output_types=types, output_shapes=shapes)


def _c4_dataset(split='train'):
  return tfds.load(
      name='c4', split=split, data_dir=_TESTDATA, shuffle_files=False)


def _spm_path():
  return os.path.join(_TESTDATA, 'sentencepiece.model')


class InputsTest(tf.test.TestCase):

  def setUp(self):
    super().setUp()
    gin.clear_config()

  def test_batch_data(self):
    dataset = ((i, i+1) for i in range(10))
    batches = inputs.batch_data(dataset, 10)
    batch = next(batches)
    self.assertEqual(len(batch), 2)
    self.assertEqual(batch[0].shape, (10,))

  def test_pad_to_max_dims(self):
    tensors1 = [np.zeros((3, 10)), np.ones((3, 10))]
    padded1 = inputs.pad_to_max_dims(tensors1)
    self.assertEqual(padded1.shape, (2, 3, 10))
    tensors2 = [np.zeros((2, 10)), np.ones((3, 9))]
    padded2 = inputs.pad_to_max_dims(tensors2)
    self.assertEqual(padded2.shape, (2, 3, 10))
    tensors3 = [np.zeros((8, 10)), np.ones((8, 9))]
    padded3 = inputs.pad_to_max_dims(tensors3, 12)
    self.assertEqual(padded3.shape, (2, 8, 12))
    tensors4 = [np.zeros((2, 10)), np.ones((3, 9))]
    padded4 = inputs.pad_to_max_dims(tensors4, 12)
    self.assertEqual(padded4.shape, (2, 4, 12))

  def test_c4_bare_preprocess_fun(self):
    dataset = _c4_dataset()

    # import pdb; pdb.set_trace()

    example = list(tfds.as_numpy(dataset.take(1)))[0]

    # Targets are NOT in the example.
    self.assertNotIn('targets', example)
    self.assertIn('text', example)
    text = example['text']

    # This should convert the dataset to an inputs/targets that are tokenized.
    dataset = inputs.c4_bare_preprocess_fun(
        dataset, spm_path=os.path.join(_TESTDATA, 'sentencepiece.model'))

    example = list(tfds.as_numpy(dataset.take(1)))[0]

    # Earlier text is now stored in targets_plaintext
    self.assertIn('targets_plaintext', example)
    self.assertEqual(example['targets_plaintext'], text)

    # Targets are now tokenized.
    self.assertIn('targets', example)
    self.assertIsInstance(example['targets'], np.ndarray)
    self.assertEqual(example['targets'].dtype, np.int64)
    self.assertGreater(len(example['targets']), 0)

    # Inputs exist but is empty because t5 preprocessors' unsupervised wasn't
    # gin configured with any.
    self.assertIn('inputs', example)
    self.assertEqual(len(example['inputs']), 0)

  def test_c4_preprocess(self):
    def load_c4_dataset(split='train'):
      dataset = _c4_dataset(split=split)
      return dataset.map(lambda example: (example, example['text']))

    def examine_processed_dataset(proc_dataset):
      count = 0
      lengths = []
      for example in tfds.as_numpy(proc_dataset):
        count += 1
        ex = example[0]
        # Targets are in the example.
        self.assertIn('targets', ex)
        self.assertEqual(ex['targets'].dtype, np.int64)
        lengths.append(len(ex['targets']))
      return count, lengths

    unfiltered_count = 0
    for example in tfds.as_numpy(load_c4_dataset()):
      unfiltered_count += 1
      # Targets are NOT in the example.
      self.assertNotIn('targets', example[0])

    proc_dataset, proc_shapes = inputs.c4_preprocess(
        load_c4_dataset(), False, ({}, ()), 2048)

    # The target shape.
    self.assertEqual(proc_shapes[1], (None,))

    # `examine_processed_dataset` has some asserts in it.
    proc_count, char_lengths = examine_processed_dataset(proc_dataset)

    # Both the original and filtered datasets have examples.
    self.assertGreater(unfiltered_count, 0)
    self.assertGreater(proc_count, 0)

    # Because we filter out some entries on length.
    self.assertLess(proc_count, unfiltered_count)

    # Preprocess using the sentencepiece model in testdata.
    spc_proc_dataset, _ = inputs.c4_preprocess(
        load_c4_dataset(), False, ({}, ()), 2048,
        tokenization='spc',
        spm_path=os.path.join(_TESTDATA, 'sentencepiece.model'))

    spc_proc_count, spc_lengths = examine_processed_dataset(spc_proc_dataset)

    # spc shortens the target sequence a lot, should be almost equal to
    # unfiltered
    self.assertLessEqual(proc_count, spc_proc_count)
    self.assertEqual(unfiltered_count, spc_proc_count)

    # Assert all spc_lengths are lesser than their char counterparts.
    for spc_len, char_len in zip(spc_lengths, char_lengths):
      self.assertLessEqual(spc_len, char_len)

  def test_c4(self):
    gin.bind_parameter(
        'shuffle_and_batch_data.preprocess_fun', inputs.c4_preprocess)
    gin.bind_parameter('c4_preprocess.max_target_length', 2048)
    gin.bind_parameter('c4_preprocess.tokenization', 'spc')
    gin.bind_parameter('c4_preprocess.spm_path',
                       os.path.join(_TESTDATA, 'sentencepiece.model'))

    gin.bind_parameter('batch_fn.batch_size_per_device', 8)
    gin.bind_parameter('batch_fn.eval_batch_size', 8)
    gin.bind_parameter('batch_fn.max_eval_length', 2048)
    gin.bind_parameter('batch_fn.buckets', ([2049], [8, 1]))

    # Just make sure this doesn't throw.
    _ = inputs.inputs(
        'c4', data_dir=_TESTDATA, input_name='targets', target_name='text')

  def t5_gin_config(self):
    # The following pages worth of gin configuration are required because a lot
    # of functions have `gin.REQUIRED` in T5 code, i.e. you cannot use these
    # functions at all without having configured gin.

    noise_density = 0.15
    max_input_length = 50
    # What preprocessors to apply - we select a random chunk of the document if
    # it exceeds a certain lengths (`select_random_chunk`), the concat multiple
    # documents together to reduce padding (`reduce_concat_tokens`), then split
    # up long examples (`split_tokens`) and finally the denoising objective
    # (`denoise`).
    gin.bind_parameter('unsupervised.preprocessors', [
        t5_processors.select_random_chunk,
        t5_processors.reduce_concat_tokens,
        t5_processors.split_tokens,
        t5_processors.denoise,
    ])

    # select_random_chunk
    gin.bind_parameter('select_random_chunk.feature_key', 'targets')
    gin.bind_parameter('select_random_chunk.max_length', max_input_length)

    # reduce_concat_tokens
    gin.bind_parameter('random_spans_helper.extra_tokens_per_span_inputs', 1)
    gin.bind_parameter('random_spans_helper.extra_tokens_per_span_targets', 1)
    gin.bind_parameter('random_spans_helper.inputs_length', max_input_length)
    gin.bind_parameter('random_spans_helper.mean_noise_span_length', 3.0)
    gin.bind_parameter('random_spans_helper.noise_density', noise_density)

    # split_tokens
    gin.bind_parameter('split_tokens.max_tokens_per_segment',
                       t5_processors.random_spans_tokens_length())

    # denoise
    gin.bind_parameter('denoise.inputs_fn',
                       t5_processors.noise_span_to_unique_sentinel)
    gin.bind_parameter('denoise.noise_density', noise_density)
    gin.bind_parameter('denoise.noise_mask_fn',
                       t5_processors.random_spans_noise_mask)
    gin.bind_parameter('denoise.targets_fn',
                       t5_processors.nonnoise_span_to_unique_sentinel)

  def test_c4_bare_preprocess_fun_denoising_objective(self):
    self.t5_gin_config()

    dataset = _c4_dataset()
    dataset, _ = inputs.c4_bare_preprocess_fun(
        dataset, spm_path=_spm_path())

    example = list(tfds.as_numpy(dataset.take(1)))[0]

    # Assertions now.

    self.assertIn('targets', example)
    targets = example['targets']
    self.assertIsInstance(targets, np.ndarray)
    self.assertEqual(targets.dtype, np.int64)
    self.assertGreater(len(targets), 0)

    self.assertIn('inputs', example)
    _inputs = example['inputs']  # pylint: disable=invalid-name
    self.assertIsInstance(_inputs, np.ndarray)
    self.assertEqual(_inputs.dtype, np.int64)
    self.assertGreater(len(_inputs), 0)

    # WHP inputs will have the bulk of the text.
    self.assertGreater(len(_inputs), len(targets))

    # WHP there will be two sentinel tokens in the inputs and targets.
    inputs_counter = collections.Counter(_inputs.tolist())
    targets_counter = collections.Counter(targets.tolist())
    self.assertEqual(1, inputs_counter[31999])
    self.assertEqual(1, inputs_counter[31998])
    self.assertEqual(1, targets_counter[31999])
    self.assertEqual(1, targets_counter[31998])

  def test_c4_pretrain(self):
    self.t5_gin_config()

    gin.bind_parameter('shuffle_and_batch_data.bare_preprocess_fun',
                       inputs.c4_bare_preprocess_fun)

    gin.bind_parameter('c4_bare_preprocess_fun.spm_path',
                       os.path.join(_TESTDATA, 'sentencepiece.model'))

    gin.bind_parameter('batch_fn.batch_size_per_device', 8)
    gin.bind_parameter('batch_fn.eval_batch_size', 8)

    gin.bind_parameter('batch_fn.max_eval_length', 50)
    gin.bind_parameter('batch_fn.buckets', ([51], [8, 1]))

    # Just make sure this doesn't throw.
    _ = inputs.inputs(
        'c4', data_dir=_TESTDATA, input_name='inputs', target_name='targets')


if __name__ == '__main__':
  tf.test.main()
