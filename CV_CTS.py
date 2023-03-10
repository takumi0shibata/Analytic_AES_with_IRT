################
# import module
################
import nltk
import os
import numpy as np
import pandas as pd
import tensorflow as tf
import random
import argparse
from configs.configs import Configs
from utils.read_data import read_word_vocab, read_essays_words_cv, read_pos_vocab, read_essays_pos_cv
from utils.general_utils import get_scaled_down_scores, pad_hierarchical_text_sequences, load_word_embedding_dict, build_embedd_table, \
                                get_overall_score_range, get_analytic_score_range, get_min_max_scores, get_attribute_mask_vector
from models.CTS import build_CTS
from utils.evaluator import evaluator

nltk.download('punkt')
nltk.download('averaged_perceptron_tagger')

def main():
    parser = argparse.ArgumentParser(description='CTS_model')
    parser.add_argument('--prompt_id', type=int, default=1, help='prompt id of essay set')
    parser.add_argument('--seed', type=int, default=12, help='set random seed')
    parser.add_argument('--input', type=str, default='word', help='word or pos')

    args = parser.parse_args()
    id = args.prompt_id
    seed = args.seed
    input_seq = args.input
    print('essay id: {}'.format(id))
    print('seed: {}'.format(seed))
    print('input: {}'.format(input_seq))

    ###################
    # Set Parameters
    ###################
    np.random.seed(seed)
    tf.random.set_seed(seed)
    random.seed(seed)
    os.environ['PYTHONHASHSEED'] = str(seed)


    configs = Configs()

    data_path = configs.DATA_PATH
    features_path = configs.FEATURES_PATH
    readability_path = configs.READABILITY_PATH
    vocab_size = configs.VOCAB_SIZE
    embedding_path = configs.EMBEDDING_PATH
    Fold = configs.FOLD
    EPOCHS = configs.EPOCHS
    BATCH_SIZE = configs.BATCH_SIZE

    num_item = len(get_min_max_scores()[id])
    overall_min, overall_max = get_overall_score_range()[id]
    overall_range = overall_max - overall_min + 1
    analytic_min, analytic_max = get_analytic_score_range()[id]
    analytic_range = analytic_max - analytic_min + 1

    ######################
    # Cross Validation
    ######################
    cv_qwk = []
    cv_lwk = []
    cv_mae = []
    cv_rmse = []
    cv_corr = []
    for fold in range(Fold):
        ##################
        # Reading Dataset
        ##################
        train_path = data_path + '{}/fold-{}/train.pkl'.format(id, fold)
        test_path = data_path + '{}/fold-{}/test.pkl'.format(id, fold)

        read_configs = {
        'train_path': train_path,
        'test_path': test_path,
        'features_path': features_path,
        'readability_path': readability_path,
        'vocab_size': vocab_size
        }

        if input_seq == 'word':
            word_vocab = read_word_vocab(read_configs)
            train_data, test_data = read_essays_words_cv(read_configs, word_vocab)

            embedd_dict, embedd_dim, _ = load_word_embedding_dict(embedding_path)
            embedd_matrix = build_embedd_table(word_vocab, embedd_dict, embedd_dim, caseless=True)
            embed_table = [embedd_matrix]
        elif input_seq == 'pos':
            pos_vocab = read_pos_vocab(read_configs)
            train_data, test_data = read_essays_pos_cv(read_configs, pos_vocab)

        max_sentlen = max(train_data['max_sentlen'], test_data['max_sentlen'])
        max_sentnum = max(train_data['max_sentnum'], test_data['max_sentnum'])

        if input_seq == 'word':
            X_train = pad_hierarchical_text_sequences(train_data['words'], max_sentnum, max_sentlen)
            X_test = pad_hierarchical_text_sequences(test_data['words'], max_sentnum, max_sentlen)
        elif input_seq == 'pos':
            X_train = pad_hierarchical_text_sequences(train_data['pos_x'], max_sentnum, max_sentlen)
            X_test = pad_hierarchical_text_sequences(test_data['pos_x'], max_sentnum, max_sentlen)

        X_train = X_train.reshape((X_train.shape[0], X_train.shape[1] * X_train.shape[2]))
        X_test = X_test.reshape((X_test.shape[0], X_test.shape[1] * X_test.shape[2]))

        X_train_linguistic_features = np.array(train_data['features_x'])
        X_test_linguistic_features = np.array(test_data['features_x'])

        X_train_readability = np.array(train_data['readability_x'])
        X_test_readability = np.array(test_data['readability_x'])

        train_data['y_scaled'] = get_scaled_down_scores(train_data['data_y'], train_data['prompt_ids'])
        test_data['y_scaled'] = get_scaled_down_scores(test_data['data_y'], test_data['prompt_ids'])

        item_mask = get_attribute_mask_vector(id)
        Y_train = np.array(train_data['y_scaled'])[:, item_mask]
        Y_test_org = np.array(test_data['data_y'])[:, item_mask]

        train_features_list = [X_train, X_train_linguistic_features, X_train_readability]
        test_features_list = [X_test, X_test_linguistic_features, X_test_readability]
        
        #################
        # Define Model
        #################
        if input_seq == 'word':
            model = build_CTS(len(word_vocab), max_sentnum, max_sentlen,
                                X_train_readability.shape[1],
                                X_train_linguistic_features.shape[1],
                                configs, Y_train.shape[1], embed_table)
        elif input_seq == 'pos':
            model = build_CTS(len(pos_vocab), max_sentnum, max_sentlen,
                                X_train_readability.shape[1],
                                X_train_linguistic_features.shape[1],
                                configs, Y_train.shape[1])

        #################
        # Training model
        #################
        eval = evaluator(num_item, overall_range, item_mask, analytic_range, test_features_list, id)
        for epoch in range(EPOCHS):
            print('Prompt ID: {}, Seed: {}, Input_seq: {}'.format(id, seed, input_seq))
            print('{} / {} EPOCHS'.format(epoch+1, EPOCHS))
            model.fit(x=train_features_list, y=Y_train, batch_size=BATCH_SIZE, epochs=1)
            eval.evaluate_from_reg(model, Y_test_org)
            eval.print_results()
        
        cv_qwk.append(eval.qwk)
        cv_lwk.append(eval.lwk)
        cv_rmse.append(eval.rmse)
        cv_mae.append(eval.mae)
        cv_corr.append(eval.corr)
        
    ####################
    # Print final info
    ####################
    cv_mean_qwk = np.mean(np.array(cv_qwk), axis=0)
    cv_mean_lwk = np.mean(np.array(cv_lwk), axis=0)
    cv_mean_rmse = np.mean(np.array(cv_rmse), axis=0)
    cv_mean_mae = np.mean(np.array(cv_mae), axis=0)
    cv_mean_corr = np.mean(np.array(cv_corr), axis=0)
    print('-' * 100)
    print('Final info')
    print(' TEST_QWK:  Mean -> {:.3f}, Each item -> {}'.format(np.mean(cv_mean_qwk), np.round(cv_mean_qwk, 3)))
    print(' TEST_LWK:  Mean -> {:.3f}, Each item -> {}'.format(np.mean(cv_mean_lwk), np.round(cv_mean_lwk, 3)))
    print(' TEST_RMSE: Mean -> {:.3f}, Each item -> {}'.format(np.mean(cv_mean_rmse), np.round(cv_mean_rmse, 3)))
    print(' TEST_MAE:  Mean -> {:.3f}, Each item -> {}'.format(np.mean(cv_mean_mae), np.round(cv_mean_mae, 3)))
    print(' TEST_CORR: Mean -> {:.3f}, Each item -> {}'.format(np.mean(cv_mean_corr), np.round(cv_mean_corr, 3)))

    ###################
    # Save outputs
    ###################
    output_path = 'outputs/CTS/{}/{}/'.format(seed, input_seq)
    os.makedirs(output_path, exist_ok=True)
    pd.DataFrame(cv_mean_qwk).to_csv(output_path + 'qwk{}.csv'.format(id), header=None, index=None)
    pd.DataFrame(cv_mean_lwk).to_csv(output_path + 'lwk{}.csv'.format(id), header=None, index=None)
    pd.DataFrame(cv_mean_rmse).to_csv(output_path + 'rmse{}.csv'.format(id), header=None, index=None)
    pd.DataFrame(cv_mean_mae).to_csv(output_path + 'mae{}.csv'.format(id), header=None, index=None)
    pd.DataFrame(cv_mean_corr).to_csv(output_path + 'corr{}.csv'.format(id), header=None, index=None)

if __name__=='__main__':
    main()