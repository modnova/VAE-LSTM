""" Implementation of the Reccurrent Variational Autoencoder """
from __future__ import absolute_import, division, print_function

import tensorboard
import tensorflow as tf
from tensorflow import layers

FLAGS = tf.app.flags.FLAGS

class RVAE(object):
    """ Builds the model graph for different modes(train, eval, predict) """
    def __init__(self, hps, vocab_size):
        self._hps = hps
        self._vsize = vocab_size #TODO: Change this/find a better way to pass in vocab_size

    def _add_source_encoder(self, input, seq_len, hidden_dim, initializer):
        """ Adds a single-layer bidirectional LSTM encoder to parse the original sentence(source_seq)
        Args:
            input: `Tensor`, input tensor of shape (batch_size, max_seq_len, emb_dim)
            seq_len: `Tensor` of (batch_size,)
            hidden_dim: `int`, size of the hidden dimension for the LSTMCell
            initializer: specify/pass initializers for variables
        Returns:
            fw_state, bw_state: Forward and backward states of the encoder with shape (batch_size, hidden_dim)
        """
        #TODO: Add highway connections(you will have to made your own)
        with tf.variable_scope('source_encoder'):
            cell_fw = tf.nn.rnn_cell.LSTMCell(hidden_dim, initializer=initializer, state_is_tuple=True)
            cell_bw = tf.nn.rnn_cell.LSTMCell(hidden_dim, initializer=initializer, state_is_tuple=True)
            (_, (fw_st, bw_st)) = tf.nn.bidirectional_dynamic_rnn(cell_fw, cell_bw, input, dtype=tf.float32, sequence_length=seq_len, swap_memory=True)
        return fw_st, bw_st

    def _add_target_encoder(self, input, fw_st, bw_st, seq_len, hidden_dim, initializer):
        """ Adds a single-layer bidirectional LSTM encoder to parse the original sentence(source_seq)
        Args:
            input: `Tensor`, input tensor of shape (batch_size, max_seq_len, emb_dim)
            fw_st: `Tensor`, fw hidden state of source encoder of shape (batch_size, hidden_dim)
            bw_st: `Tensor`, bw hidden state of source encoder of shape (batch_size, hidden_dim)
            seq_len: `Tensor` of (batch_size,)
            hidden_dim: `int`, size of the hidden dimension for the LSTMCell
            initializer: specify/pass initializers for variables
        Returns:
            fwd_state, bw_state: Forward and backward states of the encoder with shape (batch_size, hidden_dim)
        """
        #TODO: Add highway linear layers
        with tf.variable_scope('target_encoder'):
            cell_fw = tf.nn.rnn_cell.LSTMCell(hidden_dim, initializer=initializer, state_is_tuple=True)
            cell_bw = tf.nn.rnn_cell.LSTMCell(hidden_dim, initializer=initializer, state_is_tuple=True)
            (_, (fw_st, bw_st)) = tf.nn.bidirectional_dynamic_rnn(cell_fw, cell_bw, input, initial_state_fw=fw_st, initial_state_bw=bw_st, dtype=tf.float32, sequence_length=seq_len, swap_memory=True)
        return fw_st, bw_st

    def _make_posterior(self, enc_output, latent_dim, initializaer):
        """ Builds ops to calculate the posterior and sample from it
        Args:
            enc_output: `Tensor` of batch size (batch_size, hidden_dim*2) from encoder
        Returns:
            A tf.distributions.MultivariateNormalDiag representing the posterior for each seq
        """
        # get mu and sigma
        mu = tf.layers.dense(enc_output, latent_dim, kernel_initializer=initializaer) # mean with shape (batch_size, latent_dim)
        sigma = tf.layers.dense(enc_output, latent_dim, tf.nn.softplus, kernel_initializer=initializaer) # logvar with shape (batch_size, latent_dim)

        return tf.contrib.distributions.MultivariateNormalDiag(loc=mu, scale_diag=sigma)

    def decoder(self, input, mode):
        """ Creates a decoder to produce outputs """
        return NotImplementedError

    def train_op(self):
        """ Creates and adds training ops to the graph """
        return NotImplementedError

    def model_fn(self, features, labels, mode, params):
        """ Builds the graph of the model being implemented 
        Args:
            features: A Tensor or dict of Tensors to be used as features(input).
            Contains:
                'source_seq': Source sequence of shape (batch_size, max_len_seq) where max_len_seq is the max length of a seq in a batch
                'source_len': Source lengths of shape (batch_size,)
            labels: A Tensor or doct of Tensors to be used as labels. Should be blank for
            predict mode.
            Contains:
                'target_seq': Target sequence of (batch_size, max_len_seq)
                'target_len': Target lengths of shape (batch_size,)
            mode: An instance of tf.estimator.ModeKeys to be used for calls to train() and evaluate()
            params: Any additional configuration needed    
        Returns:
            tf.estimator.EstimatorSpec which is contains information the caller(i.e train(), evaluate(), predict())
            needs.
        """
        # create some global initializers
        rand_unif_init = tf.random_uniform_initializer(-1.0,1.0, seed=123)
        rand_norm_init = tf.random_normal_initializer(stddev=0.001)
        embedding_init = params['embedding_initializer']


        # embed all necessary input tensors
        with tf.variable_scope('embedding'):
            embedding = tf.get_variable('embedding', [self._vsize, self._hps.emb_dim], dtype=tf.float32, initializer=embedding_init) # initialize with pretrained word vecs
            emb_src_inputs = tf.nn.embedding_lookup(embedding, features['source_seq']) # (batch_size, max_seq_len, emb_dim)
            if mode != tf.estimator.ModeKeys.PREDICT:
                emb_dec_inputs = [tf.nn.embedding_lookup(embedding, x) for x in tf.unstack(labels['target_seq'])]
                # A list of `Tensor`s of length max_target_seq_len and shape (batch_size, emb_dim)
                if mode == tf.estimator.ModeKeys.TRAIN:
                    emb_tgt_inputs = tf.nn.embedding_lookup(embedding, labels['target_seq']) # (batch_size, max_seq_len, emb_dim) for target encoding

        # pass the embedded tensors to the source encoder
        src_fw_st, src_bw_st = self._add_source_encoder(emb_src_inputs, features['source_len'], self._hps.hidden_dim, rand_unif_init)

        # pass embedded tensors to the target encoder
        if mode == tf.estimator.ModeKeys.TRAIN:
            tgt_fw_st, tgt_bw_st = self._add_target_encoder(emb_tgt_inputs, src_fw_st, src_bw_st, labels['target_len'], self._hps.hidden_dim, rand_unif_init)
            enc_output = tf.concat([tgt_fw_st, tgt_bw_st], 1) # shape (batch_size, hidden_dim*2)
        else:
            enc_output = tf.concat([src_fw_st, src_bw_st], 1) # shape (batch_size, hidden_dim*2)

        # add the posterior distribution and sample from it
        q_z = self._make_posterior(enc_output, self._hps.latent_dim, rand_norm_init)
        z = q_z.sample()

        # add the prior distribution for loss
        p_z = tf.contrib.distributions.MultivariateNormalDiag(loc=[0.]*self._hps.latent_dim,scale_diag=[1.]*self._hps.latent_dim)

        # TODO: Add decoder

        # TODO: Computes losses with KL annealing or something




        # feed mean and std to kld


        return NotImplementedError
