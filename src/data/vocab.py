""" Prepare the data for creation of TFRecords """
from __future__ import absolute_import, division, print_function

import csv
import io
import os
from collections import Counter, defaultdict

import nltk
import numpy as np
import tensorflow as tf

# Additions of special characters for sequence generation. You may add your own...
PAD = "<PAD>"
START = "<START>"
EOS = "<EOS>"
UNK = "<UNK>"

class Vocab(object):
    """ For reading data, processing for input, and writing to TFRecords
    """
    def __init__(self, vocab_path=None):
        """ Creates a vocab from training data and/or pretrained word vectors
        Args:
            vocab_path: Path to save/load the vocab file
        """
        self.vocab = defaultdict(self._next_val) # maps tokens to ids. Autogenerate next id as needed
        self.reverse_vocab = {}
        self.token_counter = Counter() # counts token frequency

        # Add special characters to the vocab
        self.vocab[PAD] = 0
        self.vocab[START] = 1
        self.vocab[EOS] = 2
        self.vocab[UNK] = 3
        self.next = 3 # after 2 is 3 and so on...

        # Reads created vocab from file if it exists
        if vocab_path and os.path.isfile(vocab_path):
            self.load_vocab(vocab_path)


    def _next_val(self):
        self.next += 1
        return self.next

    def prep_train_seq(self, seq):
        """ Preprocesses text input and build vocabulary
        Args:
            seq: A sequence to to be prepared
        """

        seq = self._tokenize(seq)

        [self._tok_to_id(token, build_vocab=True) for token in seq]

        return

    def prep_seq(self, seq):
        """ Preprocesses text, but does not add to the vocab(see prep_train_seq for this)
        Args:
            seq: A sequence to be prepared
        Returns:
            A list of IDs
        """

        seq = self._tokenize(seq)

        seq = list(map(self._tok_to_id, seq))

        # Add START and EOS tokens
        seq.insert(0, self.vocab[START])
        seq.append(self.vocab[EOS])

        return seq

    def _tokenize(self, seq):
        """ Tokenizes the input sequence.
        Args:
            seq: A sequence to be tokenized/cleaned(e.g "Hello, this is a sequence.")
        Returns: 
            A list of tokens(e.g words or charcters)
        """
        seq = seq.lower()
        return nltk.word_tokenize(seq)

    def _tok_to_id(self, token, build_vocab=False):
        """ Maps a token to it's corresponding ID. Or if in training mode, also adds new words to the vocab
        Args:
            token: A word
            build_vocab: Adds unseen tokens to the vocab 
        Returns:
            An ID
        """

        if build_vocab:
            self.token_counter[token] += 1
            return self.vocab[token]
        elif token in self.vocab:
            self.token_counter[token] += 1
            return self.vocab[token]
        else:
            self.token_counter[UNK] += 1
            return self.vocab[UNK]

    def make_reverse_vocab(self):
        """ Makes a reverse vocab for the given vocab.
        """
        self.reverse_vocab = {id_:token for token,id_ in self.vocab.items()}

    def ids_to_text(self, id_list):
        """ Maps a sequence of IDs to a string
        Args:
            id_list: A sequence of IDs
        Returns:
            text: A text string that is supposed to be a sentence
        """

        text = ''.join(map(lambda x:self.reverse_vocab[x],id_list))
        
        return text

    def read_embeddings(self, path, load_np=True):
        """ Reads word embeddings from file that are saved in the FastText format
        Args:
            path: Path to the embedding file
            load_np: Load np array
        Returns:
            embedding_initializer: An initializer for pre-trained embeddings
        """

        if not os.path.isfile(path):
            raise Exception('ERROR! Filepath is not a file')

        print("Reading embeddings from:", path)

        def embed_initializer(shape=None, dtype=tf.float32, partition_info=None):
            assert dtype is tf.float32
            return embedding_matrix

        if load_np:
            embedding_matrix = np.load("/home/tldr/Projects/models/current/VAE-LSTM/data/external/emb_matrix.npy")
            return embed_initializer

        fin = io.open(path, 'r', encoding='utf-8', newline='\n', errors='ignore')
        _, dim = map(int, fin.readline().split())

        # read the fasttext embeddings into a dictionary
        embeddings = {}
        for line in fin:
            tokens = line.rstrip().split(' ')
            embeddings[tokens[0]] = np.asarray(tokens[1:], dtype='float32')

        # turn the embeddings dict into a numpy array
        vsize = len(self.vocab)
        embedding_matrix = np.random.uniform(-1, 1, size=(vsize, dim))
        for w, i in self.vocab.items():
            v = embeddings.get(w)
            if v is not None and i < vsize:
                embedding_matrix[i] = v

        # save to numpy array format
        np.save("/home/tldr/Projects/models/current/VAE-LSTM/data/external/emb_matrix.npy", embedding_matrix)

        return embed_initializer

    def save_vocab(self, path, max_keep=None):
        """ Saves the vocabulary to file.
        Args:
            path: Directory to save the file to
            max_keep: The maximum number of words to keep. If None, it will write all words in the vocab dict to file.
        """

        if not os.path.isdir(path):
            raise Exception('ERROR: You must specify a valid directory to store the vocab file')

        print("Writing vocab to file...")

        # sanity check the path
        path = os.path.join(path, "vocab.tsv")
        if os.path.isfile(path):
            raise Exception('WARNING: There already exists a vocab file located at the specified path. If you want to create a new vocab delete/move the old one.')

        # add all or the most frequent words to file and update vocab object
        freq_tokens = self.token_counter.most_common(max_keep)

        # rewrite self.vocab
        self.vocab.clear()
        self.vocab[PAD] = 0
        self.vocab[START] = 1
        self.vocab[EOS] = 2
        self.vocab[UNK] = 3
        self.next = 3
        for token, _ in freq_tokens:
            self.vocab[token]

        with open(path, 'w') as vocab_tsv:
            # write the first line
            writer = csv.writer(vocab_tsv, delimiter='\t')
            writer.writerow(['Word', 'ID'])
            for word, id_ in self.vocab.items():
                # skip adding special tokens
                if id_ < 4:
                    continue
                writer.writerow([word, id_])

        print("Finished writing to vocab")

        return

    def load_vocab(self, path):
        """ Loads the vocab file from the specified path
        Args:
            path: Filepath to the existing vocab
        """

        if not os.path.isfile(path):
            raise Exception("Error: A proper filepath must be specified!")

        print("Loading vocab at:", path)

        with open(path, 'r') as vocab_f:
            vocab_f = csv.reader(vocab_f, delimiter='\t')
            for idx, line in enumerate(vocab_f):
                # skip the header
                if idx == 0:
                    continue
                word = line[0]
                if word in self.vocab:
                    raise Exception("Duplicate word %s found in vocab" % word)

                # add the word to the vocab at the same ID(hopefully)
                self.vocab[word]
                if self.vocab[word] != int(line[1]):
                    raise Exception("The read word in the vocab does not match the ID it was given in the vocab file. Please check the vocab file.")
                
        return
