import torch
import numpy as np
from .MatrixNormalWishart import MatrixNormalWishart
from .MatrixNormalGamma import MatrixNormalGamma
from .Wishart import Wishart
from .DiagonalWishart import DiagonalWishart
from .utils import matrix_utils
from .MultivariateNormal_vector_format import MultivariateNormal_vector_format

class MultiLinearNormalWishart():
    # This class performs inference for a multilinear model of the form Y = A_1@X_1 + A_2@X_2 + ... + B 
    # with an approximate posterior distribution that factorizes over the A's and the B.  In order to handle the 
    # case where the different X_i's have different sizes we 

    def __init__(self,n,p_list,batch_shape=(),mask_list=None,X_mask_list=None,pad_X=False, noise_type = 'Wishart'):
        self.noise_type = noise_type
        self.pad_X = pad_X
        self.p_list = p_list.copy()
        self.n = n
        self.event_dim = 2  
        self.batch_dim = len(batch_shape)
        self.event_shape = (n,0)
        self.batch_shape = batch_shape
        if mask_list is None:
            mask_list = [None]*len(self.p_list)
        elif pad_X is True:
            mask_list.append(None)                
        if X_mask_list is None:
            X_mask_list = [None]*len(self.p_list)
        elif pad_X is True:
            X_mask_list.append(None)

        self.A = []
        if noise_type == 'Wishart':
            self.invU = Wishart((self.n+2)*torch.ones(batch_shape,requires_grad=False),torch.eye(self.n,requires_grad=False))
            for i in range(len(self.p_list)):
                self.A.append(MatrixNormalWishart(mu_0 = torch.zeros(batch_shape + (n,self.p_list[i]),requires_grad=False),mask=mask_list[i],X_mask=X_mask_list[i],fixed_precision=True,pad_X=False))
                self.A[i].invU = self.invU
        elif noise_type == 'Gamma':
            self.invU = DiagonalWishart((self.n+2)*torch.ones(batch_shape + (n,),requires_grad=False),torch.ones(batch_shape + (self.n,),requires_grad=False))
            for i in range(len(self.p_list)):
                self.A.append(MatrixNormalGamma(mu_0 = torch.zeros(batch_shape + (n,self.p_list[i]),requires_grad=False),mask=mask_list[i],X_mask=X_mask_list[i],fixed_precision=True,pad_X=False))
                self.A[i].invU = self.invU
        self.bias = torch.zeros(batch_shape + (n,1),requires_grad=False)

    def to_event(self,n):
        if n == 0:
            return self
        for i in range(len(self.p_list)):           
            self.A[i].event_dim = self.A[i].event_dim + n
            self.A[i].batch_dim = self.A[i].batch_dim - n 
            self.A[i].event_shape = self.A[i].batch_shape[-n:] + self.A[i].event_shape
            self.A[i].batch_shape = self.A[i].batch_shape[:-n]
        self.invU.to_event(n)
        return self

    def raw_update(self, X_list, Y, iters = 1, lr=1.0, beta=None):
        sample_shape = Y.shape[:-self.event_dim-self.batch_dim]
        N = torch.tensor(np.prod(sample_shape),requires_grad=False)
        N = N.expand(self.batch_shape + self.event_shape[:-2])            
        muY = Y.mean(list(range(len(sample_shape))))
        muX = []
        for i in range(len(self.p_list)):
            muX.append(X_list[i].mean(list(range(len(sample_shape)))))
     
        for _ in range(iters):
            Y_res = Y - muY
            idx = torch.randperm(len(self.p_list))
            for i in idx:
                Y_res = Y_res - self.A[i].mean()@(X_list[i]-muX[i])
            for i in idx:
                Y_res = Y_res + self.A[i].mean()@(X_list[i]-muX[i])
                self.A[i].raw_update(X_list[i]-muX[i],Y_res,lr=lr,beta=beta)
                Y_res = Y_res - self.A[i].mean()@(X_list[i]-muX[i])

            SEyy = 0.0
            bias = muY
            Y_res = 0.0
            for i in range(len(self.p_list)):
                Y_res = Y_res - self.A[i].mean()@(X_list[i])
                bias = bias - self.A[i].mean()@muX[i]
                SEyy = SEyy + (self.A[i].mu-self.A[i].mu_0)@self.A[i].invV_0@(self.A[i].mu-self.A[i].mu_0).transpose(-2,-1)
            Y_res = Y_res - bias
            SEyy = SEyy + (Y_res*Y_res.transpose(-2,-1)).sum(0)  
            while SEyy.ndim > self.event_dim + self.batch_dim:
                SEyy = SEyy.sum(0)
                
            if self.noise_type == 'Wishart':
                self.invU.ss_update(SEyy,N,lr,beta)                
            elif self.noise_type == 'Gamma':
                self.invU.ss_update(SEyy.diagonal(dim1=-1,dim2=-2),N.unsqueeze(-1),lr,beta)
            self.bias = bias*lr + self.bias*(1-lr)

    def update(self,pX,pY,p=None,lr=1.0,beta=None):



        raise NotImplementedError

    def KLqprior(self):
        KL = -self.invU.KLqprior()*(len(self.p_list)-1)
        for i in range(len(self.p_list)):
            KL = KL + self.A[i].KLqprior()
        return KL

    def Elog_like(self,X,Y): 
        raise NotImplementedError

    def Elog_like_given_pX_pY(self,pX,pY):  
        raise NotImplementedError

    def Elog_like_X(self,Y):
        raise NotImplementedError
    
    def Elog_like_X_given_pY(self,pY):
        raise NotImplementedError
    def forward(self,pX):
        raise NotImplementedError
    
    def backward(self,pY):  
        raise NotImplementedError
    
    def Ebackward(self,pY):
        raise NotImplementedError

    def predict(self,X_list):
        if self.pad_X is True:
            X_list.append(torch.ones(X_list[0].shape[:-2]+(1,1)))
        invSigmamu = 0.0
        Res = 0.0
        for i in range(len(self.p_list)):
            X=X_list[i]
            pred_i, Res_i = self.A[i].predict(X)
            invSigmamu = invSigmamu + pred_i.EinvSigmamu()
            Res = Res + Res_i
        Res = Res - (len(self.p_list)-1)*(0.5*self.ElogdetinvSigma() - 0.5*self.n*np.log(2*np.pi))

        return MultivariateNormal_vector_format(invSigma = self.EinvSigma(), invSigmamu = invSigmamu + self.EinvSigma()@self.bias), Res

    def predict_given_pX(self,pX):
        return self.forward(pX)

    def ElogdetinvSigma(self):
        return self.invU.ElogdetinvSigma()
    
    def EinvSigma(self):
        return self.invU.EinvSigma()

    def ESigma(self):
        return self.invU.ESigma()




