""" Implementation of the Reccurrent Variational Autoencoder """
from __future__ import absolute_import, division, print_function

import tensorboard
import tensorflow as tf
from tensorflow import layers

ds = tf.contrib.distributions
seq2seq = tf.contrib.seq2seq

FLAGS = tf.app.flags.FLAGS

class RVAE(object):
    """ Builds the model graph for different modes(train, eval, predict) """
    def __init__(self, hps, vocab_size):
        self._hps = hps
        self._vsize = vocab_size

    def _embedding_layer(self, input):
        """ Adds the embedding layer that is used for the encoder and decoder inputs
        Args:
            input: `Tensor` of shape (batch_size, max_seq_len)
        Returns:
            emb_tensor: `Tensor` of size (batch_size, max_seq_len, emb_dim)
        """
        with tf.variable_scope('embedding_layer', reuse=tf.AUTO_REUSE):
            embedding = tf.get_variable('embedding_tensor', [self._vsize, self._hps.emb_dim], dtype=tf.float32, initializer=self._embedding_init) # initialize with pretrained word vecs

        return tf.nn.embedding_lookup(embedding, input)

    def _embedding_helper(self, input, z):
        """ A helper for beam search decoding during predict mode
        Args:
            input: A vector `Tensor` of shape (batch_size,beam_size) but batch_size should be 1 for predict calls
            z: `Tensor` of shape (batch_size, latent_dim) used for concatonating output with sample
        Returns:
            next_dec_input: `Tensor` of shape (batch_size, beam_size, emb_dim+latent_dim)
        """

        emb_word = self._embedding_layer(input) # shape (batch_size, beam_size, emb_dim)

        z = tf.tile(tf.expand_dims(z, 1), [1,self._hps.beam_size,1]) # shape (batch_size, beam_size, latent_dim)
        next_dec_input = tf.concat([emb_word,z],-1)

        return next_dec_input

    def _word_dropout(self, seq, len, keep_prob):
        """ Creates modified decoder input that has some words in the train input replaced with the UNK token(id==3)
        Args:
            seq: `Tensor` of shape (max_dec_seq_len,) containing a sequence for one batch entry
            len: `Tensor` containing the sequence length for a batch entry
            keep_prob: `float` used to determine the amount of tokens to keep
        Returns:
            A tensor with the same shape as dec_inputs with certain tokens in each batch_size replaced with UNK
            according to some keep_prob
        """
        with tf.variable_scope('dropout'):
            # cast inputs to int64
            seq = tf.cast(seq, dtype=tf.int32)
            len = tf.cast(len, dtype=tf.int32)

            # draw Bernoulli distribution
            p = tf.distributions.Bernoulli(probs=keep_prob)

            # sample distribution
            sub_mask = p.sample(len) # shape (len) of independant Bernoulli trials
            d_mask = tf.concat([sub_mask, tf.zeros(tf.size(seq)-len, dtype=tf.int32)], 0) #dropout mask
            d_mask = tf.multiply(seq, d_mask) # set all to-be-changed indices to 0

            # get the indices with 0
            indices = tf.reshape(tf.where(tf.equal(sub_mask, 0)), [-1,1]) # get indices of 0 positions
            indices = tf.cast(indices, tf.int32) # cast to tf.int32 for op compatibility
            values = tf.fill([tf.size(indices)], 3) # get tensor of same shape of indices

            seq = tf.scatter_nd(indices=indices, updates=values, shape=tf.shape(seq)) # create inverse mask(all zeroes except for the idx(s) to replace)

            # add mask back to the inv_mask
            seq = tf.add(d_mask, seq)

            # cast outputs back to tf.int64
            seq = tf.cast(seq, dtype=tf.int64)
            len = tf.cast(len, dtype=tf.int64)

        return (seq, len)

    def _add_source_encoder(self, input, seq_len, hidden_dim):
        """ Adds a single-layer bidirectional LSTM encoder to parse the original sentence(source_seq)
        Args:
            input: `Tensor`, input tensor of shape (batch_size, max_seq_len, emb_dim)
            seq_len: `Tensor` of (batch_size,)
            hidden_dim: `int`, size of the hidden dimension for the LSTMCell
        Returns:
            fw_state, bw_state: Forward and backward states of the encoder with shape (batch_size, hidden_dim)
        """
        #TODO: Add highway connections(you will have to make your own)
        with tf.variable_scope('source_encoder'):
            cell_fw = tf.nn.rnn_cell.LSTMCell(hidden_dim, state_is_tuple=True)
            cell_bw = tf.nn.rnn_cell.LSTMCell(hidden_dim, state_is_tuple=True)
            (_, (fw_st, bw_st)) = tf.nn.bidirectional_dynamic_rnn(cell_fw, cell_bw, input, dtype=tf.float32, sequence_length=seq_len, swap_memory=True)
        return fw_st, bw_st

    def _add_target_encoder(self, input, fw_st, bw_st, seq_len, hidden_dim):
        """ Adds a single-layer bidirectional LSTM encoder to parse the original sentence(source_seq)
        Args:
            input: `Tensor`, input tensor of shape (batch_size, max_seq_len, emb_dim)
            fw_st: `Tensor`, fw hidden state of source encoder of shape (batch_size, hidden_dim)
            bw_st: `Tensor`, bw hidden state of source encoder of shape (batch_size, hidden_dim)
            seq_len: `Tensor` of (batch_size,)
            hidden_dim: `int`, size of the hidden dimension for the LSTMCell
        Returns:
            fwd_state, bw_state: Forward and backward states of the encoder with shape (batch_size, hidden_dim)
        """
        #TODO: Add highway linear layers
        with tf.variable_scope('target_encoder'):
            cell_fw = tf.nn.rnn_cell.LSTMCell(hidden_dim, state_is_tuple=True)
            cell_bw = tf.nn.rnn_cell.LSTMCell(hidden_dim, state_is_tuple=True)
            (_, (fw_st, bw_st)) = tf.nn.bidirectional_dynamic_rnn(cell_fw, cell_bw, input, initial_state_fw=fw_st, initial_state_bw=bw_st, dtype=tf.float32, sequence_length=seq_len, swap_memory=True)
        return fw_st, bw_st

    def _make_posterior(self, enc_state, latent_dim, initializaer):
        """ Builds ops to calculate the posterior and sample from it
        Args:
            enc_state: `Tensor` of batch size (batch_size, hidden_dim*2) from encoder
        Returns:
            A tf.distributions.MultivariateNormalDiag representing the posterior for each seq
        """
        # get mu and sigma
        with tf.variable_scope('posterior'):
            mu = tf.layers.dense(enc_state, latent_dim, kernel_initializer=initializaer) # mean with shape (batch_size, latent_dim)
            sigma = tf.layers.dense(enc_state, latent_dim, tf.nn.softplus, kernel_initializer=initializaer) # logvar with shape (batch_size, latent_dim)

            posterior = ds.MultivariateNormalDiag(loc=mu, scale_diag=sigma)

        return posterior

    def _add_decoder(self, enc_state, hidden_dim, num_layers, z, keep_prob, mode, initializer, sample_prob=0.0, train_inputs=None):
        """ Creates a decoder to produce outputs 
        Args: 
            enc_state: `Tensor`, Final enc_state after linear layer of shape (batch_size, hidden_dim).
              Used for the decoder's inital state
            latent_dim: `int` to specify the size of the latent_dim
            hidden_dim: `int`, size of the hidden dimension for the LSTM cell
            z: `Tensor` of shape (batch_size, latent_dim). The sampled z from the posterior distribution
            keep_prob: `float` 1-dropout rate for dropout
            mode: `tf.estimator.ModeKeys` specifies the mode and determines what ops to add to the graph
            initializer: Initializer for the projection layer
            sample_prob: `float`, Affects the sampling during train and eval modes. When 0.0(Default) the output of the previous decoder cell is not sampled,
              so the target seq is used as input with shape (batch_size, emb_dim+latent_dim), this is because input at each t is a `TensorArray`. When performing
              evaluation, the sample_prob should be 1.0 to read output at each t.
            train_inputs: A tuple of `Tensor`s that specify inputs for the decoder during training. If in prediction mode, this should be None.
            Contains:
              enc_dec_inputs: Inputs for the decoder of shape (batch_size, max_seq_len, emb_dim)
              target_len: Length of target sequences of shape (batch_size,)
        Returns:
            outputs: Differs from train/eval and predict modes
              PREDICT: Outputs predicted_ids of (batch_size, steps_decoded, beam_size)
              TRAIN/EVAL: Outputs logits of (batch_size, seq_len, vsize)
        """

        # argument validation
        if mode == tf.estimator.ModeKeys.PREDICT:
            assert train_inputs == None, 'Invalid input for PREDICT mode. train_inputs is not None'
            assert self._hps.batch_size == 1, 'Invalid batch_size for inference'
        elif mode == tf.estimator.ModeKeys.TRAIN:
            assert len(train_inputs) == 2, 'Invalid number of arguments for train input'
            assert sample_prob == 0.0, 'Invalid sample_prob for TRAIN mode. Should be 0.0'
            enc_dec_inputs, target_len = train_inputs
        else:
            assert sample_prob == 1.0, 'Invalid sample_prob for EVAL mode. Should be 1.0'
            enc_dec_inputs, target_len = train_inputs

        with tf.variable_scope('decoder'):
            # basic stacked RNN of 2 layers
            dec_cells = [tf.nn.rnn_cell.LSTMCell(hidden_dim, state_is_tuple=True) for _ in range(num_layers)]
            stacked_cell = tf.nn.rnn_cell.MultiRNNCell(dec_cells)

            # add projection layer to create unnormalized logits
            projection_layer = tf.layers.Dense(self._vsize, use_bias=False)

            if mode != tf.estimator.ModeKeys.PREDICT:
                # make inputs for decoder
                [_,seq_len,_] = enc_dec_inputs.get_shape().as_list()
                z = tf.tile(tf.expand_dims(z, 1), [1, seq_len, 1]) # (batch_size, seq_len, latent_dim)
                helper = seq2seq.ScheduledOutputTrainingHelper(enc_dec_inputs, target_len, sampling_probability=sample_prob, auxiliary_inputs=z)
                # at each t, the shape of the input will be (batch_size, latent_dim+emb_dim) after concat
                decoder = seq2seq.BasicDecoder(cell=stacked_cell,
                                               helper=helper,
                                               initial_state=enc_state,
                                               output_layer=projection_layer)
            else:
                decoder_init_state = seq2seq.tile_batch(enc_state, multiplier=self._hps.beam_size) # shape (batch_size*beam_size,)
                decoder = seq2seq.BeamSearchDecoder(cell=stacked_cell,
                                                    embedding=lambda x: self._embedding_helper(x, z),
                                                    start_tokens=tf.fill([self._hps.batch_size], 1),
                                                    end_token=2,
                                                    initial_state=decoder_init_state,
                                                    beam_width=self._hps.beam_size,
                                                    output_layer=projection_layer)

            # unroll the decoder
            outputs, _, _ = seq2seq.dynamic_decode(decoder, impute_finished=True, maximum_iterations=self._hps.max_dec_steps)

        if mode == tf.estimator.ModeKeys.TRAIN:
            return outputs.rnn_output
        else:
            return outputs.predicted_ids

    def _calc_losses(self, q_z, p_z, logits, targets, masks):
        """ Adds ops to calculate losses for training 
        Args:
            q_z: The posterior distribution for calculating KL Divergence
            p_z: The prior distribution for calculating KL Divergence
            logits: The outputs of the decoder. Shape (batch_size, tgt_max_seq_len, vsize)
            targets: `Tensor` of target values for the loss. Of shape (batch_size, tgt_max_seq_len)
            masks: `Tensor` of shape (batch_size, tgt_max_seq_len) of float type representing the padding mask
            anneal_rate: Rate for KL Divergence annealing. Helps for training
        """

        # calculate crossentropy loss (batch_size,)
        r_loss = seq2seq.sequence_loss(logits=logits, labels=targets, weights=masks, average_across_batch=False)

        # calculate KLD (batch_size,)
        kl_div = ds.kl_divergence(q_z, p_z)

        # calculate total loss
        loss = tf.reduce_mean(r_loss, name='r_loss') + 42 * tf.reduce_mean(kl_div, name='kl_loss') # TODO: Change this so that it incorporates KL annealing



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
                'target_seq': Target sequence of (batch_size, max_len_seq) used for decoder input
                'target_len': Target lengths of shape (batch_size,)
                'decoder_tgt': Target sequence of (batch_size, max_len_seq). Last shape is same as target_seq
            mode: An instance of tf.estimator.ModeKeys to be used for calls to train() and evaluate()
            params: Any additional configuration needed    
        Returns:
            tf.estimator.EstimatorSpec which is contains information the caller(i.e train(), evaluate(), predict())
            needs.
        """
        # create some global initializers
        rand_unif_init = tf.random_uniform_initializer(-1.0,1.0, seed=123)
        rand_norm_init = tf.random_normal_initializer(stddev=0.001)
        trunc_norm_init = tf.truncated_normal_initializer(stddev=0.0001)
        if mode == tf.estimator.ModeKeys.TRAIN:
            self._embedding_init = params['embedding_initializer']
            # apply word dropout, replacing 0.3 words in decoder input with UNK token
            dec_input,_ = tf.map_fn(lambda x: self._word_dropout(x[0], x[1], self._hps.keep_prob), (labels['target_seq'], labels['target_len']))
            emb_tgt_inputs = self._embedding_layer(dec_input)

        # embed all necessary input tensors
        emb_src_inputs = self._embedding_layer(features['source_seq'])

        # pass the embedded tensors to the source encoder
        src_fw_st, src_bw_st = self._add_source_encoder(emb_src_inputs, features['source_len'], self._hps.hidden_dim)
        src_enc_state = tf.concat([src_fw_st, src_bw_st], 1) # shape (batch_size, hidden_dim*2)

        # pass embedded tensors to the target encoder
        if mode == tf.estimator.ModeKeys.TRAIN:
            tgt_fw_st, tgt_bw_st = self._add_target_encoder(emb_tgt_inputs, src_fw_st, src_bw_st, labels['target_len'], self._hps.hidden_dim)
            train_enc_state = tf.concat([tgt_fw_st, tgt_bw_st], 1) # shape (batch_size, hidden_dim*2)
            # calculate posterior with train_enc_state
            q_z = self._make_posterior(train_enc_state, self._hps.latent_dim, rand_norm_init)
        else:
            # add the posterior distribution and sample from it
            q_z = self._make_posterior(src_enc_state, self._hps.latent_dim, rand_norm_init)
        z = q_z.sample() # shape (batch_size, latent_dim)

        # add the prior distribution for loss
        if mode != tf.estimator.ModeKeys.PREDICT:
            p_z = ds.MultivariateNormalDiag(loc=[0.]*self._hps.latent_dim,scale_diag=[1.]*self._hps.latent_dim)

        # transform encoder state shape to (batch_size, hidden_dim)
        dec_init_state = tf.layers.dense(src_enc_state, self._hps.hidden_dim, kernel_initializer=rand_unif_init)

        # add the decoder for the given mode
        if mode == tf.estimator.ModeKeys.TRAIN:
            logits = self._add_decoder(dec_init_state, self._hps.hidden_dim, self._hps.dec_layers, z, self._hps.keep_prob, trunc_norm_init, mode, (emb_tgt_inputs, labels['target_len']))
            training_logits = tf.identity(logits, name='logits')
        else:
            predicted_ids = self._add_decoder(dec_init_state, self._hps.hidden_dim, self._hps.dec_layers, z, self._hps.keep_prob, trunc_norm_init, mode)
            inference_logits = tf.identity(predicted_ids, name='predictions')

        # TODO: Computes losses with KL annealing or something
        if mode != tf.estimator.ModeKeys.PREDICT:
            masks = tf.sequence_mask(labels['target_len'], dtype=tf.float32, name='masks')
            losses = self._calc_losses(q_z, p_z, training_logits, labels['decoder_tgt'], masks)

        return NotImplementedError
