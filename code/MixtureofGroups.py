import numpy as np
from scipy.stats import entropy
from sklearn.decomposition import KernelPCA, PCA, TruncatedSVD
from sklearn.mixture import BayesianGaussianMixture, GaussianMixture
from sklearn.cluster import DBSCAN,AffinityPropagation, MiniBatchKMeans, KMeans
from sklearn.preprocessing import StandardScaler
from sklearn import metrics
import gc, keras, time, sys, math

from .learning_models import LogisticRegression_Sklearn,LogisticRegression_Keras,MLP_Keras
from .learning_models import default_CNN,default_RNN,CNN_simple, RNN_simple, default_CNN_text, default_RNN_text, Clonable_Model #deep learning
from .representation import *
from .utils import softmax,estimate_batch_size

def build_conf_Yvar(y_obs_var, T_idx, Z_val):
    """ From variable length arrays of annotations and indexs"""
    T = np.max(np.concatenate(T_idx))+1
    N = y_obs_var.shape[0]
    Kl = np.max(Z_val) +1
    aux_confe_matrix = np.ones((T,Kl,Kl))
    for i in range(N): #independiente de "T"
        for l, t_idx in enumerate(T_idx[i]):
            obs_t = y_obs_var[i][l].argmax(axis=-1)
            aux_confe_matrix[t_idx, Z_val[i], obs_t] +=1
    aux_confe_matrix_n = aux_confe_matrix/aux_confe_matrix.sum(axis=-1,keepdims=True)
    return aux_confe_matrix, aux_confe_matrix_n #return both: normalized and unnormalized

def aux_clusterize(data_to_cluster,M,DTYPE_OP='float32',option="hard",l=0.005):
    """ Clusterize data """
    print("Doing clustering...",end='',flush=True)
    std = StandardScaler()
    data_to_cluster = std.fit_transform(data_to_cluster) 
        
    kmeans = MiniBatchKMeans(n_clusters=M, random_state=0,init='k-means++',batch_size=128)
    #KMeans(M,init='k-means++', n_jobs=-1,random_state=0)
    kmeans.fit(data_to_cluster)
    distances = kmeans.transform(data_to_cluster)

    if option=="fuzzy":
        probas_t = np.zeros_like(distances,dtype=DTYPE_OP)
        for t in range(probas_t.shape[0]):
            for m in range(probas_t.shape[1]):
                m_fuzzy = 1.2
                probas_t[t,m] = 1/(np.sum( np.power((distances[t,m]/(distances[t,:]+keras.backend.epsilon())), 2/(m_fuzzy-1)) ) + keras.backend.epsilon())
    elif option == "softmax":
        probas_t = softmax(-(distances+keras.backend.epsilon())/l).astype(DTYPE_OP)
    elif option == "softmax inv":
        probas_t = softmax(1/(l*distances+keras.backend.epsilon())).astype(DTYPE_OP)
    elif option == 'hard':
        probas_t = keras.utils.to_categorical(kmeans.labels_,num_classes=M)
    print("Done!")
    return probas_t
            
def clusterize_annotators(y_o,M,no_label=-1,bulk=True,cluster_type='mv_close',data=[],model=None,DTYPE_OP='float32',BATCH_SIZE=64,option="hard",l=0.005):
    start_time = time.time()
    if bulk: #Individual scenario --variable y_o
        if cluster_type == 'previous':
            A_rep_aux = y_o
        else:
            T_idx = data[0]
            mv_soft = data[1]
            Kl  = mv_soft.shape[1]
            conf_mat, conf_mat_norm  = build_conf_Yvar(y_o, T_idx, mv_soft.argmax(axis=-1))
            if cluster_type == 'flatten':
                A_rep_aux = conf_mat_norm.reshape(conf_mat_norm.shape[0], Kl**2) #flatten
            elif cluster_type == 'js': 
                A_rep_aux = np.zeros((conf_mat.shape[0], Kl))
                for t in range(A_rep_aux.shape[0]):
                    A_rep_aux[t] = JS_confmatrixs(conf_mat_norm[t], np.identity(Kl),raw=True) #distancia a I (MV)

        probas_t = aux_clusterize(A_rep_aux,M,DTYPE_OP,option) #labels_kmeans
        alphas_init = probas_t

    else: #Global scenario: y_o: is soft-MV
        mv_soft = y_o.copy()
        if cluster_type=='loss': #cluster respecto to loss function
            obj_clone = Clonable_Model(model)
            aux_model = obj_clone.get_model()
            aux_model.compile(loss='categorical_crossentropy',optimizer=model.optimizer)
            aux_model.fit(data, mv_soft, batch_size=BATCH_SIZE,epochs=30,verbose=0)
            predicted = aux_model.predict(data,verbose=0)
        elif cluster_type == 'mv_close':
            predicted = np.clip(mv_soft, keras.backend.epsilon(), 1.)
       
        data_to_cluster = []
        for i in range(mv_soft.shape[0]):
            for j in range(mv_soft.shape[1]):
                ob = np.tile(keras.backend.epsilon(), mv_soft.shape[1])
                ob[j] = 1
                true = np.clip(predicted[i],keras.backend.epsilon(),1.)      
                f_l = distance_function(true, ob)  #funcion de distancia o similaridad
                data_to_cluster.append(f_l)  
        data_to_cluster = np.asarray(data_to_cluster)
        #if manny classes or low entropy?
        model = PCA(n_components=min(3,mv_soft.shape[1]) ) # 2-3
        data_to_cluster = model.fit_transform(data_to_cluster) #re ejecutar todo con esto
        probas_t = aux_clusterize(data_to_cluster,M,DTYPE_OP,option,l)
        alphas_init = probas_t.reshape(mv_soft.shape[0],mv_soft.shape[1],M)

    print("Get init alphas in %f mins"%((time.time()-start_time)/60.) )
    return alphas_init

def distance_function(predicted,ob):
    return -predicted*np.log(ob) #CE raw

def project_and_cluster(y_o,M_to_try=20,anothers_visions=True,DTYPE_OP='float32',printed=True,mode_project="pca"):
    ###another way to cluster..
    if len(y_o.shape) == 2:
        M_itj = categorical_representation(y_o,no_label =-1)
    else:
        M_itj = y_o.copy()
    data_to_cluster = M_itj.transpose(1,0,2).reshape(M_itj.shape[1],M_itj.shape[0]*M_itj.shape[2])
    data_to_cluster = data_to_cluster.astype(DTYPE_OP)
    
    if mode_project.lower() == "pca":
        model = PCA(n_components=4)
    elif mode_project.lower() == "tpca":
        model = TruncatedSVD(n_components=4)
    elif mode_project.lower() == "kpca":
        model = KernelPCA(n_components=4, kernel='rbf', n_jobs=-1)

    plot_data = model.fit_transform(data_to_cluster)
    to_return = [plot_data]
    
    if printed:
        model = BayesianGaussianMixture(n_components=M_to_try)
        model.fit(plot_data)
        M_founded = len(set(np.argmax(model.predict_proba(plot_data),axis=1))) 
        print("Bayesian gaussian mixture say is %d clusters "%M_founded)

        if anothers_visions:
            X_sim = metrics.pairwise_distances(plot_data,metric='euclidean',n_jobs=-1)
            #dos indicadores de numero de cluster
            model = DBSCAN(eps=np.mean(X_sim), min_samples=5, metric='precomputed', n_jobs=-1)
            model.fit(X_sim)
            print("DBSCAN say is %d clusters"%len(set(model.labels_)))
            model = AffinityPropagation(affinity='precomputed')
            model.fit(X_sim)
            print("Affinity Propagation say is %d clusters"%len(set(model.labels_)))

        to_return.append( M_founded )
    return to_return


"""
    MIXTURE MODEL NOT KNOWING THE IDENTITY
    >>> CMM (CROWD MIXTURE OF MODEL) <<<
"""

class GroupMixtureGlo(object): 
    def __init__(self,input_dim,Kl,M=2,epochs=1,optimizer='adam',dtype_op='float32'): 
        if type(input_dim) != tuple:
            input_dim = (input_dim,)
        self.input_dim = input_dim
        self.Kl = Kl #number of classes of the problem
        #params
        self.M = M #groups of annotators
        self.epochs = epochs
        self.optimizer = optimizer
        self.DTYPE_OP = dtype_op
        
        self.Keps = keras.backend.epsilon() 
        self.priors=False #boolean of priors
        self.compile=False
        self.lambda_random = False
        
    def get_basemodel(self):
        return self.base_model
    def get_confusionM(self):
        """Get confusion matrices of every group p(yo|g,z)"""  
        return self.betas.copy()
    def get_alpha(self):
        """Get alpha param, p(g) globally"""
        return self.alphas.copy()
    def get_qestimation(self):
        """Get Q estimation param, this is Q_ij(g,z) = p(g,z|xi,y=j)"""
        return self.Qij_mgamma.copy()
        
    def define_model(self,tipo,start_units=1,deep=1,double=False,drop=0.0,embed=[],BatchN=False,h_units=128, glo_p=False):
        """Define the base model and other structures"""
        self.type = tipo.lower()     
        if self.type =="sklearn_shallow" or self.type =="sklearn_logistic":
            self.base_model = LogisticRegression_Sklearn(self.epochs)
            self.compile = True
            return
        
        if self.type == "keras_shallow" or 'perceptron' in self.type: 
            self.base_model = LogisticRegression_Keras(self.input_dim,self.Kl)
            #It's not a priority, since HF has been shown to underperform RMSprop and Adagrad, while being more computationally intensive.
            #https://github.com/keras-team/keras/issues/460
        elif self.type=='defaultcnn' or self.type=='default cnn':
            self.base_model = default_CNN(self.input_dim,self.Kl)
        elif self.type=='defaultrnn' or self.type=='default rnn':
            self.base_model = default_RNN(self.input_dim,self.Kl)
        elif self.type=='defaultcnntext' or self.type=='default cnn text': #with embedding
            self.base_model = default_CNN_text(self.input_dim[0],self.Kl,embed) #len is the length of the vocabulary
        elif self.type=='defaultrnntext' or self.type=='default rnn text': #with embedding
            self.base_model = default_RNN_text(self.input_dim[0],self.Kl,embed) #len is the length of the vocabulary

        elif self.type == "ff" or self.type == "mlp" or self.type=='dense': #classic feed forward
            print("Needed params (units,deep,drop,BatchN?)") #default activation is relu
            self.base_model = MLP_Keras(self.input_dim,self.Kl,start_units,deep,BN=BatchN,drop=drop)
        elif self.type=='simplecnn' or self.type=='simple cnn' or 'cnn' in self.type:
            print("Needed params (units,deep,drop,double?,BatchN?)") #default activation is relu
            self.base_model = CNN_simple(self.input_dim,self.Kl,start_units,deep,double=double,BN=BatchN,drop=drop,dense_units=h_units,global_pool=glo_p)
        elif self.type=='simplernn' or self.type=='simple rnn' or 'rnn' in self.type:
            print("Needed params (units,deep,drop,embed?)")
            self.base_model = RNN_simple(self.input_dim,self.Kl,start_units,deep,drop=drop,embed=embed,len=0,out=start_units*2)
            #and what is with embedd
        self.base_model.compile(optimizer=self.optimizer,loss='categorical_crossentropy') 
        self.max_Bsize_base = estimate_batch_size(self.base_model)
        self.compile = True
        
    def get_predictions(self,X):
        """Return the predictions of the model if is from sklearn or keras"""
        if "sklearn" in self.type:
            return self.base_model.predict_proba(X) 
        else:
            return self.base_model.predict(X,batch_size=self.max_Bsize_base) #fast predictions

    def define_priors(self,priors):
        """
            Priors with shape: (M,K,K), need counts for every group and every pair (k,k) ir global (M,K)
            The group m, given a class "k" is probably that say some class
            it is recomended that has full of ones
        """
        if type(priors) == str:
            if priors == "laplace":
                self.Apriors = self.Bpriors = 1
            else:
                print("Prior string do not understand")
                return
        else:
            if len(priors.shape)==2:
                self.Bpriors = np.expand_dims(priors,axis=2)
                print("Remember to prior alphas")
            elif len(priors.shape) == 1: 
                self.Apriors = priors.copy()
                print("Remember to prior bethas")
        self.priors = True

    def init_E(self,X,r):
        """Realize the initialziation of the E step on the EM algorithm"""
        start_time = time.time()
        #-------> init Majority voting        
        mv_probs_j = majority_voting(r,repeats=True,probas=True) # soft -- p(y=j|xi)

        #-------> init alpha
        self.alpha_init = clusterize_annotators(mv_probs_j,M=self.M,bulk=False,cluster_type='mv_close',DTYPE_OP=self.DTYPE_OP) #clusteriza en base mv
        #self.alpha_init = clusterize_annotators(mv_probs_j,M=self.M,bulk=False,cluster_type='loss',data=X,model=self.base_model,DTYPE_OP=self.DTYPE_OP,BATCH_SIZE=batch_size) #clusteriza en base aloss
        
         #-------> Initialize p(z=gamma|xi,y=j,g): Combination of mv and belive observable
        lambda_group = np.ones((self.M),dtype=self.DTYPE_OP)  #or zeros
        #if self.lambda_random:
        #    for m in range(self.M):
        #        lambda_group[m] = np.random.beta(1,1)
        print("Lambda by group: ",lambda_group)
        #Zijm = np.zeros((self.N,self.Kl,self.M,self.Kl),dtype=self.DTYPE_OP)
        #for j_ob in range(self.Kl):
        #    onehot = np.tile(self.Keps, self.Kl)
        #    onehot[j_ob] = 1. #all belive in the observable
        #    Zijm[:,j_ob,:,:] = (lambda_group*mv_probs_j[:,:,None] + (1-lambda_group)*onehot[None,:,None]).transpose(0,2,1)
        #-------> init q_ij      
        #self.Qij_mgamma = self.alpha_init[:,:,:,None]*Zijm
        self.Qij_mgamma = self.alpha_init[:,:,:,None]*mv_probs_j[:,None,None,:]

        #-------> init betas
        self.betas = np.zeros((self.M,self.Kl,self.Kl),dtype=self.DTYPE_OP) 

        #-------> init alphas
        self.alphas = np.zeros((self.M),dtype=self.DTYPE_OP)
        
        if self.pre_init_z != 0:
            print("Pre-train network on %d epochs..."%(self.pre_init_z),end='',flush=True)
            self.base_model.fit(X,mv_probs_j,batch_size=self.batch_size,epochs=self.pre_init_z,verbose=0)
            #reset optimizer but hold weights--necessary for stability 
            self.base_model.compile(loss='categorical_crossentropy',optimizer=self.optimizer)
            print(" Done!")
        print("Alphas: ",self.alphas.shape)
        print("MV init: ",mv_probs_j.shape)
        print("Betas: ",self.betas.shape)
        print("Q estimate: ",self.Qij_mgamma.shape)
        self.init_exectime = time.time()-start_time
        
    def E_step(self,predictions):
        """ Realize the E step in matrix version"""       
        p_new = np.log( np.clip(predictions, self.Keps,1.))[:,None,None,:] #safe logarithmn
        a_new = np.log( np.clip(self.alphas, self.Keps,1.))[None,None,:,None] #safe logarithmn
        b_new = (np.log( np.clip(self.betas, self.Keps,1.))[None,:,:,:]).transpose(0,3,1,2) #safe logarithmn
        
        self.Qij_mgamma = np.exp(p_new + a_new + b_new)
        self.aux_for_like = (self.Qij_mgamma.sum(axis=-1)).sum(axis=-1) #p(y=j|x) --marginalized
        self.Qij_mgamma = self.Qij_mgamma/self.aux_for_like[:,:,None,None] #normalize
        
    def M_step_old(self,X,r): 
        """ Realize the M step"""
        #-------> base model
        Qij_gamma = self.Qij_mgamma.sum(axis=-2) #qij(gamma)
        r_estimate = np.zeros((self.N,self.Kl),dtype=self.DTYPE_OP) #create the repeat "estimate"/"ground truth"
        for i in range(self.N):
            r_estimate[i] = np.tensordot(Qij_gamma[i],r[i],axes=[[0],[0]])

        if "sklearn" in self.type:#train to learn p(z|x)
            self.base_model.fit(X, np.argmax(r_estimate,axis=1) ) 
        else:
            self.base_model.fit(X,r_estimate,batch_size=self.batch_size,epochs=self.epochs,verbose=0) 
    
        #-------> alpha 
        Qij_m = self.Qij_mgamma.sum(axis=-1) #qij(m)
        self.alphas = np.tensordot(Qij_m, r, axes=[[0,1],[0,1]]) # sum_ij r_ij(g) = Qij_m[i]*r[i] 
        if self.priors:
            self.alphas += self.Apriors
        self.alphas = self.alphas/self.alphas.sum(axis=-1,keepdims=True) #p(g) -- normalize

        #-------> beta
        for j_ob in range(self.Kl):
            self.betas[:,:,j_ob] = np.tensordot(self.Qij_mgamma[:,j_ob,:,:],r[:,j_ob], axes=[[0],[0]]) # ~p(yo=j|g,z)              
        if self.priors:
            self.betas += self.Bpriors #priors has to be shape: (M,Kl,Kl)--read define-prior functio
        self.betas = self.betas/self.betas.sum(axis=-1,keepdims=True) #normalize (=p(yo|g,z))

    def M_step(self,X,r): 
        """ Realize the M step"""
        QRij_mgamma = self.Qij_mgamma* r[:,:,None,None]
        #-------> base model
        r_estimate = QRij_mgamma.sum(axis=(1,2))
        if "sklearn" in self.type:#train to learn p(z|x)
            self.base_model.fit(X, np.argmax(r_estimate,axis=1) ) 
        else:
            self.base_model.fit(X,r_estimate,batch_size=self.batch_size,epochs=self.epochs,verbose=0) 
    
        #-------> alpha 
        self.alphas = QRij_mgamma.sum(axis=(0,1,3)) 
        if self.priors:
            self.alphas += self.Apriors
        self.alphas = self.alphas/self.alphas.sum(axis=-1,keepdims=True) #p(g) -- normalize

        #-------> beta
        self.betas = (QRij_mgamma.sum(axis=0)).transpose(1,2,0)            
        if self.priors:
            self.betas += self.Bpriors #priors has to be shape: (M,Kl,Kl)--read define-prior functio
        self.betas = self.betas/self.betas.sum(axis=-1,keepdims=True) #normalize (=p(yo|g,z))

    def compute_logL(self,r,predictions):
        """ Compute the log-likelihood of the optimization schedule"""
        return np.tensordot(r , np.log(self.aux_for_like+self.Keps))+0. #safe logarithm
                                                  
    def train(self,X_train,r_train, pre_init_z=0,batch_size=64,max_iter=500,relative=True,tolerance=3e-2):
        if not self.compile:
            print("You need to create the model first, set .define_model")
            return
        print("Initializing new EM...")
        self.pre_init_z = pre_init_z
        self.batch_size = batch_size
        self.N = X_train.shape[0]
        self.init_E(X_train,r_train) #maybe init outside..
        
        logL = []
        stop_c = False
        tol,old_betas,old_alphas = np.inf,np.inf,np.inf
        self.current_iter = 1
        self.alphas_training = []
        while(not stop_c):
            print("Iter %d/%d\nM step:"%(self.current_iter,max_iter),end='',flush=True)
            start_time = time.time()
            self.M_step(X_train,r_train)
            self.alphas_training.append(self.alphas.copy())
            print(" done,  E step:",end='',flush=True)
            predictions = self.get_predictions(X_train) #p(z|x) 
            self.E_step(predictions)          
            self.current_exectime = time.time()-start_time
            print(" done //  (in %.2f sec)\t"%(self.current_exectime),end='',flush=True)
            logL.append(self.compute_logL(r_train,predictions))
            print("logL: %.3f\t"%(logL[-1]),end='',flush=True)
            if self.current_iter>=2:
                tol = np.abs(logL[-1] - logL[-2])                    
                if relative:
                    tol = tol/np.abs(logL[-2])
                tol2 = np.mean(np.abs(self.betas.flatten()-old_betas)/(old_betas+self.Keps)) #confusion
                tol3 = np.mean(np.abs(self.alphas-old_alphas)/(old_alphas+self.Keps)) #alphas
                print("Tol1: %.5f\tTol2: %.5f\tTol3: %.5f\t"%(tol,tol2,tol3),end='',flush=True)
            old_betas = self.betas.flatten().copy()         
            old_alphas = self.alphas.copy()
            self.current_iter+=1
            print("")
            if self.current_iter>max_iter or (tol<=tolerance and tol2<=tolerance): #alphas fuera: and tol3<=tolerance
                stop_c = True 
        print("Finished training!")
        return np.asarray(logL)
    
    def stable_train(self,X,r,pre_init_z=0,batch_size=64,max_iter=50,tolerance=3e-2):
        """
            A stable schedule to train a model on this formulation
        """
        #self.lambda_random = False #lambda=1
        if not self.priors:
            self.define_priors('laplace') #needed..
        logL_hist = self.train(X,r,pre_init_z=pre_init_z,batch_size=batch_size,max_iter=max_iter,tolerance=tolerance,relative=True)
        return logL_hist
    
    #and multiples runs with lambda random false?
    def multiples_run(self,Runs,X,r,pre_init_z=0,batch_size=64,max_iter=50,tolerance=3e-2): 
        """
            Run multiples max_iter of EM algorithm, with random stars
        """
        if Runs==1:
            return self.stable_train(X,r,pre_init_z=pre_init_z,batch_size=batch_size,max_iter=max_iter,tolerance=tolerance), 0
        #maybe lamda random here
        if not self.priors:
            self.define_priors('laplace') #needed!
            
        found_betas = []
        found_alphas = []
        found_model = [] #quizas guardar pesos del modelo
        found_logL = []
        iter_conv = []

        if type(self.base_model.layers[0]) == keras.layers.InputLayer:
            obj_clone = Clonable_Model(self.base_model) #architecture to clone
        else:
            it = keras.layers.Input(shape=self.base_model.input_shape[1:])
            obj_clone = Clonable_Model(self.base_model, input_tensors=it) #architecture to clon

        for run in range(Runs):
            self.base_model = obj_clone.get_model() #reset-weigths
            self.base_model.compile(loss='categorical_crossentropy',optimizer=self.optimizer)

            logL_hist = self.train(X,r,pre_init_z=pre_init_z,batch_size=batch_size,max_iter=max_iter,tolerance=tolerance,relative=True) #here the models get resets
            
            found_betas.append(self.betas.copy())
            found_alphas.append(self.alphas.copy())
            found_model.append(self.base_model.get_weights()) #revisar si se resetean los pesos o algo asi..
            found_logL.append(logL_hist)
            iter_conv.append(self.current_iter-1)
            
            del self.base_model
            keras.backend.clear_session()
            gc.collect()
        #setup the configuration with maximum log-likelihood
        logL_iter = np.asarray([np.max(a) for a in found_logL])
        indexs_sort = np.argsort(logL_iter)[::-1] 
        
        self.betas = found_betas[indexs_sort[0]].copy()
        self.alphas = found_alphas[indexs_sort[0]].copy()
        self.base_model = obj_clone.get_model() #change
        self.base_model.set_weights(found_model[indexs_sort[0]])
        self.E_step(self.get_predictions(X)) #to set up Q
        print("Multiples runs over Ours Global, Epochs to converge= ",np.mean(iter_conv))
        return found_logL,indexs_sort[0]

    def annotations_2_group(self,annotations,data=[],pred=[],no_label_sym = -1):
        """
            Map some annotations to some group model by the confusion matrices, p(g| {x_l,y_l})
        """
        if len(pred) != 0:
            predictions_m = pred #if prediction_m is passed
        elif len(data) !=0: 
            predictions_m = self.get_predictions_groups(data) #if data is passed
        else:
            print("Error, in order to match annotations to a group you need pass the data X or the group predictions")
            return
            
        result = np.log(self.get_alpha()+self.Keps) #si les saco Keps?
        aux_annotations = [(i,annotation) for i, annotation in enumerate(annotations) if annotation != no_label_sym]
        for i, annotation in aux_annotations:
            if annotation != no_label_sym: #if label it
                for m in range(self.M):
                    result[m] += np.log(predictions_m[i,m,annotation]+self.Keps)
        result = np.exp(result - result.max(axis=-1, keepdims=True) ) #invert logarithm in safe way
        return result/result.sum()
    
    def get_predictions_group(self,m,X):
        """ Predictions of group "m", p(y^o | xi, g=m) """
        p_z = self.get_predictions(X)
        p_y_m = np.zeros(p_z.shape)
        for i in range(self.N):
            p_y_m[i] = np.tensordot(p_z[i,:] ,self.betas[m,:,:],axes=[[0],[0]] ) # sum_z p(z|xi) * p(yo|z,g=m)
        return p_y_m 
    
    def get_predictions_groups(self,X,data=[]):
        """ Predictions of all groups , p(y^o | xi, g) """
        if len(data) != 0:
            p_z = data
        else:
            p_z = self.get_predictions(X)
        predictions_m = np.tensordot(p_z ,self.betas,axes=[[1],[1]] ) #sum_z p(z|xi) * p(yo|z,g)
        return predictions_m#.transpose(1,0,2)

    def calculate_extra_components(self,X,y_o,T,calculate_pred_annotator=True,p_z=[]):
        """
            Measure indirect probabilities through bayes and total probability of annotators
        """
        predictions_m = self.get_predictions_groups(X,data=p_z) #p(y^o|x,g=m)
        
        prob_Gt = np.zeros((T,self.M)) #p(g|t)
        for t in range(T):
            prob_Gt[t] = self.annotations_2_group(y_o[:,t],pred=predictions_m) 

        prob_Yzt = np.tensordot(prob_Gt, self.get_confusionM(),axes=[[1],[0]])  #p(y^o|z,t) = sum_g p(g|t) * p(yo|z,g)
  
        prob_Yxt = None
        if calculate_pred_annotator:
            prob_Yxt = np.tensordot(predictions_m, prob_Gt, axes=[[1],[1]]).transpose(0,2,1) #p(y^o|x,t) = sum_g p(g|t) *p(yo|x,g)            
        return predictions_m, prob_Gt, prob_Yzt, prob_Yxt
    
    def calculate_Yz(self):
        """ Calculate global confusion matrix"""
        return np.sum(self.betas*self.alphas[:,None,None],axis=0)
    
    def get_annotator_reliability(self,y_o,X,t):
        """Get annotator reliability, based on his annotations:"""        
        prob_Gt = annotations_2_group(self,y_o[:,t],data=X)
        
        prob_Yzt = np.tensordot(prob_Gt, self.get_confusionM(),axes=[[0],[0]])  #p(y^o|z,t) = sum_g p(g|t) * p(yo|z,g)
        return prob_Yzt #do something with it


"""
    MIXTURE MODEL KNOWING IDENTITY
"""

class GroupMixtureInd(object):
    def __init__(self,input_dim,Kl,M=2,epochs=1,optimizer='adam',dtype_op='float32'): 
        if type(input_dim) != tuple:
            input_dim = (input_dim,)
        self.input_dim = input_dim
        self.Kl = Kl #number of classes of the problem
        #params
        self.M = M #groups of annotators
        self.epochs = epochs
        self.optimizer = optimizer
        self.DTYPE_OP = dtype_op
        
        self.Keps = keras.backend.epsilon() 
        self.priors = False #boolean of priors
        self.compile_z = False
        self.compile_g = False
        self.lambda_random = False
        
    def get_basemodel(self):
        return self.base_model
    def get_confusionM(self):
        """Get confusion matrices of every group p(y|g,z)"""  
        return self.betas.copy()
    def get_qestimation(self):
        """Get Q estimation param, this is Q_il(g,z) = p(g,z|x_i,a_il, r_il)"""
        return self.reshape_il(self.Qil_mgamma.copy())
        
    def define_model(self,tipo,start_units=1,deep=1,double=False,drop=0.0,embed=[],BatchN=True,h_units=128,glo_p=False):
        """Define the base model p(z|x) and other structures"""
        self.type = tipo.lower()     
        if self.type =="sklearn_shallow" or self.type =="sklearn_logistic":
            self.base_model = LogisticRegression_Sklearn(self.epochs)
            self.compile_z = True
            return

        if self.type == "keras_shallow" or 'perceptron' in self.type: 
            self.base_model = LogisticRegression_Keras(self.input_dim,self.Kl)
            #It's not a priority, since HF has been shown to underperform RMSprop and Adagrad, while being more computationally intensive.
            #https://github.com/keras-team/keras/issues/460
        elif self.type=='defaultcnn' or self.type=='default cnn':
            self.base_model = default_CNN(self.input_dim,self.Kl)
        elif self.type=='defaultrnn' or self.type=='default rnn':
            self.base_model = default_RNN(self.input_dim,self.Kl)
        elif self.type=='defaultcnntext' or self.type=='default cnn text': #with embedding
            self.base_model = default_CNN_text(self.input_dim[0],self.Kl,embed) #len is the length of the vocabulary
        elif self.type=='defaultrnntext' or self.type=='default rnn text': #with embedding
            self.base_model = default_RNN_text(self.input_dim[0],self.Kl,embed) #len is the length of the vocabulary
        elif self.type == "ff" or self.type == "mlp" or self.type=='dense': #classic feed forward
            print("Needed params (units,deep,drop,BatchN?)") #default activation is relu
            self.base_model = MLP_Keras(self.input_dim,self.Kl,start_units,deep,BN=BatchN,drop=drop)
        elif self.type=='simplecnn' or self.type=='simple cnn' or 'cnn' in self.type:
            print("Needed params (units,deep,drop,double?,BatchN?)") #default activation is relu
            self.base_model = CNN_simple(self.input_dim,self.Kl,start_units,deep,double=double,BN=BatchN,drop=drop,dense_units=h_units,global_pool=glo_p)
        elif self.type=='simplernn' or self.type=='simple rnn' or 'rnn' in self.type:
            print("Needed params (units,deep,drop,embed?)")
            self.base_model = RNN_simple(self.input_dim,self.Kl,start_units,deep,drop=drop,embed=embed,len=0,out=start_units*2)
            #and what is with embedd
        self.base_model.compile(optimizer=self.optimizer,loss='categorical_crossentropy') 
        self.max_Bsize_base = estimate_batch_size(self.base_model)
        self.compile_z = True
        
    def define_model_group(self, tipo, input_dim, start_units=64, deep=1, drop=0.0, BatchN=False, bias=False, embed=False, embed_M=[]):
        #define model over annotators -- > p(g|a)
        if type(input_dim) != tuple:
            input_dim = (input_dim,)
        type_g = tipo.lower()  

        if type_g == "keras_shallow" or 'perceptron' in type_g: 
            self.group_model = LogisticRegression_Keras(input_dim, self.M, bias, embed=embed, embed_M=embed_M, BN=BatchN)
        elif type_g == "ff" or type_g == "mlp" or type_g=='dense': #classic feed forward
            print("Needed params (units,deep,drop,BatchN?)") #default activation is relu
            self.group_model = MLP_Keras(input_dim, self.M, start_units, deep, BN=BatchN,drop=drop, embed=embed, embed_M=embed_M)
        self.group_model.compile(optimizer=self.optimizer,loss='categorical_crossentropy') 
        self.max_Bsize_group = estimate_batch_size(self.group_model)
        self.compile_g = True
        
    def get_predictions_z(self,X):
        """Return the predictions of the model p(z|x) if is from sklearn or keras"""
        if "sklearn" in self.type:
            return self.base_model.predict_proba(X) 
        else:
            return self.base_model.predict(X, batch_size=self.max_Bsize_base) #self.batch_size

    def get_predictions_g(self,A, same_shape=True):
        """Return the predictions of the model p(g|a) if is from parameter or model"""
        if len(A.shape) != 1:
            if not self.compile_g: 
                return self.alphas_t[np.squeeze(A)] 
            else:
                return self.group_model.predict(A, batch_size=self.max_Bsize_group)

        else: #come from batch of examples..
            print("YA NO DEBERIA ENTRAR ACA..... ERROR!!!!!!!!!!!!!")
            return
            #A = self.flatten_il(A)
            #to_return = self.aux_pred_g(A)
            #if same_shape:
            #    to_return = self.reshape_il(to_return) #add some execution time... carefully
            #return to_return

    def flatten_il(self,array):
        return np.concatenate(array)

    def reshape_il(self,array):
        to_return = []
        sum_Ti_n1 = 0
        for i in range(self.N):
            sum_Ti_n   = sum_Ti_n1
            sum_Ti_n1  = sum_Ti_n1 + self.T_i_all[i]
            to_return.append( array[sum_Ti_n : sum_Ti_n1] )
        del array
        gc.collect()
        return np.asarray(to_return)

    def define_priors(self,priors):
        """
            Priors with shape: (M,K,K), need counts for every group and every pair (k,k) ir global (M,K)
            The group m, given a class "k" is probably that say some class
            it is recomended that has full of ones
        """
        if type(priors) == str:
            if priors == "laplace":
                self.Bpriors = 1
            else:
                print("Prior string do not understand")
                return
        else:
            if len(priors.shape)==2:
                self.Bpriors = np.expand_dims(priors,axis=2)
            else:
                print("Error on prior")
        self.priors = True

    def init_E(self,X,Y_ann, T_idx, A=[]):
        """Realize the initialziation of the E step on the EM algorithm"""
        start_time = time.time()
        #-------> init Majority voting ---
        mv_probs_j = majority_voting(Y_ann,repeats=False,probas=True) # soft -- p(y=j|xi)
        
        #------->init p(g|a)
        info_A = True
        if len(A) == 0:
            info_A = False
        else:
            if np.cumprod(A,axis=0, dtype=self.DTYPE_OP).sum() <= self.Keps: #if vectors are orthonormals
                info_A = False
        if not info_A:
            print("A = Son ortonormales / No fueron entregados!")
            probas_t =  clusterize_annotators(Y_ann,M=self.M,bulk=True,cluster_type='flatten',data=[T_idx,mv_probs_j],DTYPE_OP=self.DTYPE_OP)
        else:
            probas_t =  clusterize_annotators(A,M=self.M,bulk=True,cluster_type='previous',DTYPE_OP=self.DTYPE_OP)
        
        #-------> init q_il
        #-------> Initialize p(z=gamma|xi,y=j,g): Combination of mv and belive observable
        lambda_group = np.ones((self.M),dtype=self.DTYPE_OP)  #or zeros
        #if self.lambda_random:
        #    for m in range(self.M):
        #        lambda_group[m] = np.random.beta(1,1)
        print("Lambda by group: ",lambda_group)
        #Zilm =  [] #np.zeros((self.N,self.Kl,self.M,self.Kl),dtype=self.DTYPE_OP)
        #for i in range(self.N): 
        #    Zilm.append( (mv_probs_j[i][None,:,None]*lambda_group + Y_ann[i][:,:,None]*(1-lambda_group) ).transpose(0,2,1) )
        #Zilm = np.asarray(Zilm)
        
        self.Qil_mgamma = []
        self.alpha_init = []
        for i in range(self.N):
            t_idxs = T_idx[i]
            self.alpha_init.append( probas_t[t_idxs] )#preinit over alphas
            self.Qil_mgamma.append( probas_t[t_idxs][:,:,None] * mv_probs_j[i][None,None,:] )  #y si hago sum con log?    
        self.Qil_mgamma = self.flatten_il(self.Qil_mgamma)

        #-------> init betas
        self.betas = np.zeros((self.M,self.Kl,self.Kl),dtype=self.DTYPE_OP) 

        if not self.compile_g:
            self.alphas_t = np.zeros((self.T,self.M), dtype=self.DTYPE_OP)
        else:
            if self.pre_init_g != 0:
                print("Pre-train networks over *g* on %d epochs..."%(self.pre_init_g),end='',flush=True)
                if len(A) == 0: #use T instead
                    A_aux = T_idx
                else:
                    A_aux, _ = get_A_il(T_idx, A=A, index=True) 
                self.group_model.fit(self.flatten_il(A_aux), self.flatten_il(self.alpha_init),batch_size=math.ceil(self.batch_size*self.T_i),epochs=self.pre_init_g,verbose=0)         
                self.group_model.compile(loss='categorical_crossentropy',optimizer=self.optimizer) #reset optimizer but hold weights--necessary for stability  
                print(" Done!")
        if self.pre_init_z != 0:
            print("Pre-train networks over *z* on %d epochs..."%(self.pre_init_z),end='',flush=True)
            self.base_model.fit(X,mv_probs_j,batch_size=self.batch_size,epochs=self.pre_init_z,verbose=0)
            self.base_model.compile(loss='categorical_crossentropy',optimizer=self.optimizer) #reset optimizer but hold weights--necessary for stability   
            print(" Done!")
        print("MV init: ",mv_probs_j.shape)
        print("Betas: ",self.betas.shape)
        print("Q estimate: ",self.Qil_mgamma.shape)
        gc.collect()
        self.init_exectime = time.time()-start_time
       
    def E_step(self, Y_ann, z_pred, g_pred, T_idx):
        """ Realize the E step in matrix version"""   
        if len(Y_ann.shape) ==1:
            Y_ann = self.flatten_il(Y_ann)
        g_pred = np.log( np.clip(g_pred, self.Keps, 1.) ) #safe logarithmn
        z_pred = np.log( np.clip(z_pred, self.Keps, 1.) ) #safe logarithmn
        b_aux = np.tensordot(Y_ann, np.log(self.betas + self.Keps),axes=[[1],[2]]) #safe logarithmn

        #g_aux2, _ = get_A_il(T_idx, A=g_pred, index=True)  #repeat p(g|a) on index of A
        lim_sup = 0
        for i, T_i in enumerate(self.T_i_all):
            lim_sup  += T_i
            lim_inf  = lim_sup - T_i
            b_new    = b_aux [ lim_inf:lim_sup ]
            g_aux    = g_pred[ T_idx[i] ] #get groups of annotators at indexs

            self.Qil_mgamma[lim_inf: lim_sup] = np.exp( z_pred[i][None,None,:] + g_aux[:,:,None] + b_new)  
        self.aux_for_like = self.Qil_mgamma.sum(axis=(1,2)) #p(y|x,a) --marginalized
        self.Qil_mgamma   = self.Qil_mgamma/self.aux_for_like[:,None,None] #normalize
    
    def M_step(self,X, Y_ann, A): 
        """ Realize the M step, A could be index or representation for every annotator T"""
        if len(Y_ann.shape) ==1:
            Y_ann = self.flatten_il(Y_ann)

        #-------> base model  
        r_estimate = np.zeros((self.N,self.Kl),dtype=self.DTYPE_OP)
        lim_sup = 0
        for i, T_i in enumerate(self.T_i_all):#range(self.N): 
            lim_sup  += T_i
            r_estimate[i] = self.Qil_mgamma[lim_sup-T_i : lim_sup].sum(axis=(0,1)) #create the "estimate"/"ground truth"
        if "sklearn" in self.type:#train to learn p(z|x)
            self.base_model.fit(X, np.argmax(r_estimate,axis=1) ) 
        else:
            self.base_model.fit(X, r_estimate, batch_size=self.batch_size,epochs=self.epochs,verbose=0) 

        #-------> alpha 
        Qil_m_flat = self.Qil_mgamma.sum(axis=-1)  #qil(m)
        if not self.compile_g:        
            for t in range(self.T):
                self.alphas_t[t] = np.tensordot( Qil_m_flat, A==t, axes=[[0],[0]] )
            self.alphas_t = self.alphas_t/self.alphas_t.sum(axis=-1,keepdims=True)
        else:
            self.group_model.fit(A, Qil_m_flat, batch_size=math.ceil(self.batch_size*self.T_i),epochs=self.epochs,verbose=0) #batch should be prop to T_i

        #-------> beta
        self.betas =  np.tensordot(self.Qil_mgamma, Y_ann , axes=[[0],[0]]) # ~p(yo=j|g,z) 
        if self.priors:
            self.betas += self.Bpriors #priors has to be shape: (M,Kl,Kl)--read define-prior functio
        self.betas = self.betas/self.betas.sum(axis=-1,keepdims=True) #normalize (=p(yo|g,z))

    def compute_logL(self):
        """ Compute the log-likelihood of the optimization schedule"""
        return np.sum( np.log(self.aux_for_like+self.Keps) )  #safe logarithm
                                                  
    def train(self,X_train,Y_ann_train, T_idx, A_train=[], pre_init_z=0, pre_init_g=0, batch_size=64,max_iter=500,relative=True,tolerance=3e-2):
        if not self.compile_z:
            print("You need to create the model first, set .define_model")
            return
        if len(Y_ann_train.shape) != 1 or len(T_idx.shape) != 1:
            print("ERROR! Needed Y and T_idx in variable length array")
            return
        self.pre_init_z = pre_init_z
        self.pre_init_g = pre_init_g
        self.batch_size = batch_size
        self.N = X_train.shape[0]
        self.T_i_all = [y_ann.shape[0] for y_ann in Y_ann_train] 
        self.T_i = np.mean(self.T_i_all)
        self.T = max(list(map(max,T_idx)))+1 #not important...--> is important for clustering
        print("Initializing new EM...")
        self.init_E(X_train,Y_ann_train,T_idx, A=A_train)
       
        if len(A_train) == 0: #use T instead
            A_2_pred = np.arange(self.T).reshape(-1,1) #self.flatten_il(T_idx).reshape(-1,1)  
            A_train = T_idx.copy()
        else:
            A_2_pred = A_train
            A_train, _ = get_A_il(T_idx, A=A_train, index=True)  #repeat p(g|a) on index of A   
        #flatten for E and M step
        Y_ann_train, A_train = self.flatten_il(Y_ann_train), self.flatten_il(A_train)

        logL = []
        stop_c = False
        tol,old_betas = np.inf,np.inf
        self.current_iter = 1
        self.alphas_training = []
        while(not stop_c):
            start_time = time.time()
            print("Iter %d/%d\nM step:"%(self.current_iter,max_iter),end='',flush=True)
            self.M_step(X_train, Y_ann_train, A_train)
            print(" done,  E step:",end='',flush=True)
            predictions_z = self.get_predictions_z(X_train) #p(z|x)
            predictions_g = self.get_predictions_g(A_2_pred) #p(g|a)
            self.alphas_training.append(predictions_g)
            self.E_step(Y_ann_train, predictions_z, predictions_g, T_idx)
            self.current_exectime = time.time()-start_time
            print(" done //  (in %.2f sec)\t"%(self.current_exectime),end='',flush=True)
            logL.append(self.compute_logL())
            print("logL: %.3f\t"%(logL[-1]),end='',flush=True)
            if self.current_iter>=2:
                tol = np.abs(logL[-1] - logL[-2])                    
                if relative:
                    tol = tol/np.abs(logL[-2])
                tol2 = np.mean(np.abs(self.betas.flatten()-old_betas)/(old_betas+self.Keps)) #confusion
                print("Tol1: %.5f\tTol2: %.5f\t"%(tol,tol2),end='',flush=True)
            old_betas = self.betas.flatten().copy()         
            self.current_iter+=1
            print("")
            if self.current_iter>max_iter or (tol<=tolerance and tol2<=tolerance):
                stop_c = True 
        print("Finished training!")
        return np.asarray(logL)
    
    def stable_train(self,X,Y_ann,T_idx, A=[], pre_init_z=0, pre_init_g=0, batch_size=64,max_iter=50,tolerance=3e-2):
        """
            A stable schedule to train a model on this formulation
        """
        if not self.priors:
            self.define_priors('laplace') #needed..
        logL_hist = self.train(X,Y_ann,T_idx, A_train=A, pre_init_z=pre_init_z,pre_init_g=pre_init_g,batch_size=batch_size,max_iter=max_iter,tolerance=tolerance)
        return logL_hist
    
    def multiples_run(self,Runs,X,Y_ann,T_idx, A=[],pre_init_z=0,pre_init_g=0, batch_size=64,max_iter=50,tolerance=3e-2): 
        """
            Run multiples max_iter of EM algorithm, same start
        """
        if Runs==1:
            return self.stable_train(X,Y_ann,T_idx, A=A,pre_init_z=pre_init_z,pre_init_g=pre_init_g,batch_size=batch_size,max_iter=max_iter,tolerance=tolerance), 0

        if not self.priors:
            self.define_priors('laplace') #needed!
            
        found_betas = []
        found_model_g = []
        found_model_z = []
        found_logL = []
        iter_conv = []
        if type(self.base_model.layers[0]) == keras.layers.InputLayer:
            obj_clone_z = Clonable_Model(self.base_model) #architecture to clone
        else:
            it = keras.layers.Input(shape=self.base_model.input_shape[1:])
            obj_clone_z = Clonable_Model(self.base_model, input_tensors=it) #architecture to clon

        if self.compile_g: 
            if type(self.group_model.layers[0]) == keras.layers.InputLayer:
                obj_clone_g = Clonable_Model(self.group_model) #architecture to clone
            else:
                it = keras.layers.Input(shape=self.group_model.input_shape[1:])
                obj_clone_g = Clonable_Model(self.group_model, input_tensors=it) #architecture to clon

        for run in range(Runs):
            self.base_model = obj_clone_z.get_model() #reset-weigths
            self.base_model.compile(loss='categorical_crossentropy',optimizer=self.optimizer)
            
            if self.compile_g: 
                self.group_model = obj_clone_g.get_model() #reset-weigths
                self.group_model.compile(loss='categorical_crossentropy',optimizer=self.optimizer)

            logL_hist = self.train(X,Y_ann,T_idx, A_train=A, pre_init_z=pre_init_z,pre_init_g=pre_init_g, batch_size=batch_size,max_iter=max_iter,tolerance=tolerance) #here the models get resets
            
            found_betas.append(self.betas.copy())
            if self.compile_g: 
                found_model_g.append(self.group_model.get_weights())
            else:
                found_model_g.append(self.alphas_t.copy())
            found_model_z.append(self.base_model.get_weights()) 
            found_logL.append(logL_hist)
            iter_conv.append(self.current_iter-1)
            
            del self.base_model
            if self.compile_g:
                self.group_model
            keras.backend.clear_session()
            gc.collect()
        #setup the configuration with maximum log-likelihood
        logL_iter = np.asarray([np.max(a) for a in found_logL])
        indexs_sort = np.argsort(logL_iter)[::-1] 
        
        self.betas = found_betas[indexs_sort[0]].copy()
        self.base_model = obj_clone_z.get_model()
        self.base_model.set_weights(found_model_z[indexs_sort[0]])
        if self.compile_g:
            self.group_model = obj_clone_g.get_model()
            self.group_model.set_weights(found_model_g[indexs_sort[0]]) 
        else:
            self.alphas_t = found_model_g[indexs_sort[0]].copy()
        A_2_pred = A
        if len(A_2_pred) == 0: #use T instead
            A_2_pred = np.arange(self.T).reshape(-1,1) #self.flatten_il(T_idx).reshape(-1,1)
        self.E_step(Y_ann, self.get_predictions_z(X), self.get_predictions_g(A_2_pred), T_idx) #to set up Q
        print("Multiples runs over Ours Individual, Epochs to converge= ",np.mean(iter_conv)) #maybe change name
        return found_logL,indexs_sort[0]

    def get_predictions_group(self,m,X):
        """ Predictions of group "m", p(y^o | xi, g=m) """
        p_z = self.get_predictions_z(X)
        p_y_m = np.zeros(p_z.shape)
        for i in range(self.N):
            p_y_m[i] = np.tensordot(p_z[i,:] ,self.betas[m,:,:],axes=[[0],[0]] ) # sum_z p(z|xi) * p(yo|z,g=m)
        return p_y_m 
    
    def get_predictions_groups(self,X,data=[]):
        """ Predictions of all groups , p(y^o | xi, g) """
        if len(data) != 0:
            p_z = data
        else:
            p_z = self.get_predictions_z(X)
        predictions_m = np.tensordot(p_z ,self.betas,axes=[[1],[1]] ) #sum_z p(z|xi) * p(yo|z,g)
        return predictions_m

    def calculate_extra_components(self,X, A, calculate_pred_annotator=True,p_z=[],p_g=[]):
        """
            Measure indirect probabilities through bayes and total probability of annotators
        """
        predictions_m = self.get_predictions_groups(X,data=p_z) #p(y^o|x,g=m)
        
        if len(p_g) != 0:
            prob_Gt = p_g
        else:
            prob_Gt = self.get_predictions_g(A)

        prob_Yzt = np.tensordot(prob_Gt, self.get_confusionM(),axes=[[1],[0]])  #p(y^o|z,t) = sum_g p(g|t) * p(yo|z,g)
    
        prob_Yxt = None
        if calculate_pred_annotator:
            prob_Yxt = np.tensordot(predictions_m, prob_Gt, axes=[[1],[1]]).transpose(0,2,1) #p(y^o|x,t) = sum_g p(g|t) *p(yo|x,g)            
        return predictions_m, prob_Gt, prob_Yzt, prob_Yxt
    
    def calculate_Yz(self,prob_Gt):
        """ Calculate global confusion matrix"""
        alphas = np.mean(prob_Gt, axis=0)
        return np.sum(self.betas*alphas[:,None,None],axis=0)
    
    def get_annotator_reliability(self,X,a):
        """Get annotator reliability, based on his representation:"""
        if len(a.shape) ==1:
            a = [a]     
        prob_Gt = self.get_predictions_g(a)[0]
        prob_Yzt = np.tensordot(prob_Gt, self.get_confusionM(),axes=[[0],[0]])  #p(y^o|z,t) = sum_g p(g|t) * p(yo|z,g)
        return prob_Yzt #do something with it