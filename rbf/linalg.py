'''
Module for linear algebra routines. 
'''
import logging
import warnings

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from scipy.linalg.lapack import (dpotrf, dpotrs, dtrtrs, dgetrf,
                                 dgetrs)

LOGGER = logging.getLogger(__name__)

try:
  from sksparse import cholmod
  HAS_CHOLMOD = True
  
except ImportError:
  HAS_CHOLMOD = False
  CHOLMOD_MSG = (
    'Could not import CHOLMOD. Sparse matrices will be converted to '
    'dense for all Cholesky decompositions. To install CHOLMOD and its '
    'python wrapper, follow the instructions at '
    'https://scikit-sparse.readthedocs.io. Anaconda users can install '
    'CHOLMOD with the command `conda install -c conda-forge '
    'scikit-sparse`')
  LOGGER.debug(CHOLMOD_MSG)


## Wrappers for low level LAPACK functions
#####################################################################
def _lu(A):
  '''
  Computes the LU factorization of `A` using the routine `dgetrf`

  Parameters
  ----------
  A : (N, N) float array

  Returns
  -------
  (N, N) float array
    LU factorization

  (N,) int array
    pivots
  '''
  # handle rank zero matrix
  if A.shape == (0, 0):
    return (np.zeros((0, 0), dtype=float), 
            np.zeros((0,), dtype=np.int32))
            
  # get the LU factorization  
  fac, piv, info = dgetrf(A)
  if info < 0:
    raise ValueError(
      'the %s-th argument had an illegal value' % -info)

  elif info > 0:
    raise np.linalg.LinAlgError(
      'U(%s, %s) is exactly zero. The factorization '
      'has been completed, but the factor U is exactly '
      'singular, and division by zero will occur if it is used '
      'to solve a system of equations. ' % (info, info))

  return fac, piv


def _solve_lu(fac, piv, b):
  '''
  Solves the system of equations `Ax = b` given the LU factorization
  of `A`. Uses the `dgetrs` routine.

  Parameters
  ----------
  fac : (N, N) float array
  piv : (N,) int array
  b : (N, *) float array

  Returns
  -------
  (N, *) float array
  '''
  # handle the case of an array with zero-length for an axis.
  if any(i == 0 for i in b.shape):
    return np.zeros(b.shape)

  x, info = dgetrs(fac, piv, b)
  if info != 0:
    raise ValueError(
      'the %s-th argument had an illegal value' % -info)

  return x


def _cholesky(A, lower=True):
  ''' 
  Computes the Cholesky decomposition of `A` using the routine
  `dpotrf`.

  Parameters
  ----------
  A : (N, N) float array
  lower : bool, optional

  Returns
  -------
  (N, N) float array
  '''
  # handle rank zero matrix
  if A.shape == (0, 0):
    return np.zeros((0, 0), dtype=float)

  L, info = dpotrf(A, lower=lower)
  if info > 0:
    raise np.linalg.LinAlgError(
      'The leading minor of order %s is not positive definite, and '
      'the factorization could not be completed. ' % info)

  elif info < 0:
    raise ValueError(
      'The %s-th argument has an illegal value.' % -info)

  return L


def _solve_cholesky(L, b, lower=True):
  ''' 
  Solves the system of equations `Ax = b` given the Cholesky
  decomposition of `A`. Uses the routine `dpotrs`.

  Parameters
  ----------
  L : (N, N) float array
  b : (N, *) float array

  Returns
  -------
  (N, *) float array
  '''
  if any(i == 0 for i in b.shape):
    return np.zeros(b.shape)

  x, info = dpotrs(L, b, lower=lower)
  if info < 0:
    raise ValueError(
      'The %s-th argument has an illegal value.' % -info)
  
  return x
  

def _solve_triangular(L, b, lower=True):
  ''' 
  Solve the triangular system of equations `Lx = b` using `dtrtrs`.

  Parameters
  ----------
  L : (N, N) float array
  b : (N, *) float array

  Returns
  -------
  (N, *) float array
  '''
  if any(i == 0 for i in b.shape):
    return np.zeros(b.shape)

  x, info = dtrtrs(L, b, lower=lower)
  if info < 0:
    raise ValueError(
      'The %s-th argument had an illegal value' % (-info))

  elif info > 0:
    raise np.linalg.LinAlgError(
      'The %s-th diagonal element of A is zero, indicating that '
      'the matrix is singular and the solutions X have not been '
      'computed.' % info)

  return x


#####################################################################
def as_sparse_or_array(A, dtype=None, copy=False):
  ''' 
  If `A` is a scipy sparse matrix then return it as a csc matrix.
  Otherwise, return it as an array.
  '''
  if sp.issparse(A):
    # This does not make a copy if A is csc, has the same dtype and
    # copy is false.
    A = sp.csc_matrix(A, dtype=dtype, copy=copy)

  else:
    A = np.array(A, dtype=dtype, copy=copy)

  return A


def as_array(A, dtype=None, copy=False):
  ''' 
  Return `A` as an array if it is not already. This properly handles
  when `A` is sparse.
  '''
  if sp.issparse(A):
    A = A.toarray()

  A = np.array(A, dtype=dtype, copy=copy)
  return A


class _SparseSolver(object):
  '''
  computes the LU factorization of the sparse matrix `A` with SuperLU.
  '''
  def __init__(self, A):
    LOGGER.debug('computing the LU decomposition of a %s by %s '
                 'sparse matrix with %s nonzeros ' % 
                 (A.shape + (A.nnz,)))
    self.factor = spla.spilu(A)

  def solve(self, b):
    ''' 
    solves `Ax = b` for `x`
    '''
    return self.factor.solve(b)


class _DenseSolver(object):
  '''
  computes the LU factorization of the dense matrix `A`.
  '''
  def __init__(self, A):
    fac, piv = _lu(A)
    self.fac = fac
    self.piv = piv

  def solve(self, b):
    ''' 
    solves `Ax = b` for `x`
    '''
    return _solve_lu(self.fac, self.piv, b)     
  

class Solver(object):
  '''
  Computes an LU factorization of `A` and provides a method to solve
  `Ax = b` for `x`. `A` can be a scipy sparse matrix or a numpy array.
  '''
  def __init__(self, A):
    ''' 
    Parameters
    ----------
    A : (N, N) array or scipy sparse matrix 
    '''
    A = as_sparse_or_array(A, dtype=float)    
    if sp.issparse(A):
      self._solver =  _SparseSolver(A)

    else:  
      self._solver = _DenseSolver(A)           
    
  def solve(self, b):
    '''
    solves `Ax = b` for `x`
    
    Parameters
    ----------
    b : (N, *) array or sparse matrix
    
    Returns
    -------
    out : (N, *) array
    '''
    b = as_array(b, dtype=float)
    return self._solver.solve(b)
  
    
class _SparsePosDefSolver(object):
  ''' 
  Factors the sparse positive definite matrix `A` as `LL^T = A`. Note
  that `L` is NOT necessarily the lower triangular matrix from a
  Cholesky decomposition. Instead, it is structured to be maximally
  sparse. This class requires CHOLMOD. 
  '''
  def __init__(self, A):
    LOGGER.debug('computing the Cholesky decomposition of a %s by %s '
                 'sparse matrix with %s nonzeros ' % 
                 (A.shape + (A.nnz,)))
    self.factor = cholmod.cholesky(A,
                                   use_long=False,
                                   ordering_method='default')
    # store the squared diagonal components of the cholesky
    # factorization
    self.d = self.factor.D() 
    # store the permutation array, which permutes `A` such that its
    # cholesky factorization is maximally sparse
    self.p = self.factor.P()

  def solve(self, b):
    ''' 
    solves `Ax = b` for `x`
    '''
    return self.factor.solve_A(b)

  def solve_L(self, b):
    ''' 
    Solves `Lx = b` for `x`
    '''
    if b.ndim == 1:
      s_inv = 1.0/np.sqrt(self.d)

    elif b.ndim == 2:
      # expand for broadcasting
      s_inv = 1.0/np.sqrt(self.d)[:, None]

    else:
      raise ValueError('`b` must be a one or two dimensional array')

    out = s_inv*self.factor.solve_L(b[self.p])
    return out

  def L(self):
    '''Return the factorization `L`'''
    L = self.factor.L()
    p_inv = np.argsort(self.p)
    out = L[p_inv]
    return out

  def log_det(self):
    '''Returns the log determinant of `A`'''
    out = np.sum(np.log(self.d))
    return out


class _DensePosDefSolver(object):
  ''' 
  Computes to Cholesky factorization of the dense positive definite
  matrix `A`. This uses low level LAPACK functions
  '''
  def __init__(self, A):
    self.chol = _cholesky(A, lower=True)

  def solve(self, b):
    ''' 
    Solves the equation `Ax = b` for `x`
    '''
    return _solve_cholesky(self.chol, b, lower=True)

  def solve_L(self, b):
    ''' 
    Solves the equation `Lx = b` for `x`, where `L` is the Cholesky
    decomposition.
    '''
    return _solve_triangular(self.chol, b, lower=True)

  def L(self):
    '''Returns the Cholesky decomposition of `A`'''
    return self.chol

  def log_det(self):
    '''Returns the log determinant of `A`'''
    out = 2*np.sum(np.log(np.diag(self.chol)))
    return out


class PosDefSolver(object):
  '''
  Factors the positive definite matrix `A` as `LL^T = A` and provides
  an efficient method for solving `Ax = b` for `x`. Additionally
  provides a method to solve `Lx = b`, get the log determinant of `A`,
  and get `L`. `A` can be a scipy sparse matrix or a numpy array.
  '''
  def __init__(self, A):
    '''
    Parameters
    ----------
    A : (N, N) array or scipy sparse matrix
      Positive definite matrix
    ''' 
    A = as_sparse_or_array(A, dtype=float)
    if sp.issparse(A) & (not HAS_CHOLMOD):
      warnings.warn(CHOLMOD_MSG)
      A = A.toarray()

    if sp.issparse(A):
      self._solver =  _SparsePosDefSolver(A)

    else:  
      self._solver = _DensePosDefSolver(A)           

  def solve(self, b):
    '''
    solves `Ax = b` for `x`
    
    Parameters
    ----------
    b : (N, *) array or sparse matrix
    
    Returns
    -------
    out : (N, *) array
    '''
    b = as_array(b, dtype=float)
    return self._solver.solve(b)

  def solve_L(self, b):
    '''
    solves `Lx = b` for `x`
    
    Parameters
    ----------
    b : (N, *) array or sparse matrix
    
    Returns
    -------
    out : (N, *) array
    '''
    b = as_array(b, dtype=float)
    return self._solver.solve_L(b)

  def L(self):
    '''
    Returns the factorization `L`
    
    Returns
    -------
    (N, N) array or sparse matrix
    '''
    return self._solver.L()

  def log_det(self):
    '''
    Returns the log determinant of `A`

    Returns
    -------
    float
    '''
    return self._solver.log_det()


def is_positive_definite(A):
  ''' 
  Tests if *A* is positive definite. This is done by testing whether
  the Cholesky decomposition finishes successfully. `A` can be a scipy
  sparse matrix or a numpy array.
  '''
  try:
    PosDefSolver(A).L()

  except (np.linalg.LinAlgError,
          cholmod.CholmodNotPositiveDefiniteError):
    return False
  
  return True


class PartitionedSolver(object):
  ''' 
  Solves the system of equations
  
     |  A   B  | | x |   | a | 
     | B.T  0  | | y | = | b |


  for `x` and `y`. This class builds the system and then factors it
  with an LU decomposition. As opposed to `PartitionedPosDefSolver`,
  `A` is not assumed to be positive definite. `A` can be a scipy
  sparse matrix or a numpy array. `B` can also be a scipy sparse
  matrix or a numpy array but it will be converted to a numpy array.
  '''
  def __init__(self, A, B):
    # make sure A is either a csc sparse matrix or a float array
    A = as_sparse_or_array(A, dtype=float)
    # ensure B is dense 
    B = as_array(B, dtype=float)
    n, p = B.shape
    if n < p:
      raise np.linalg.LinAlgError(
        'There are fewer rows than columns in `B`. This makes the '
        'block matrix singular, and its inverse cannot be computed.')

    # concatenate the A and B matrices 
    if sp.issparse(A):
        Z = sp.csc_matrix((p, p), dtype=float)
        C = sp.vstack((sp.hstack((A, B)),
                       sp.hstack((B.T, Z))))
    else:
        Z = np.zeros((p, p), dtype=float)
        C = np.vstack((np.hstack((A, B)),
                       np.hstack((B.T, Z))))
          
    self._solver = Solver(C)
    self.n = n
    
  def solve(self, a, b):
    ''' 
    Solves for `x` and `y` given `a` and `b`.
    
    Parameters
    ----------
    a : (N, *) array or sparse matrix
    b : (P, *) array or sparse matrix
    
    Returns
    -------
    (N, *) array
    (P, *) array
    '''
    a = as_array(a, dtype=float)
    b = as_array(b, dtype=float)
    c = np.concatenate((a, b), axis=0)
    xy = self._solver.solve(c)
    x, y = xy[:self.n], xy[self.n:]
    return x, y
  

class PartitionedPosDefSolver(object):
  ''' 
  Solves the system of equations
  
     |  A   B  | | x |   | a | 
     | B.T  0  | | y | = | b |


  for `x` and `y`, where `A` is a positive definite matrix. Rather
  than naively building and solving the system, this class
  partitions the inverse as
  
     |  C   D  |
     | D.T  E  |
     
  where 
  
    C = A^-1 - (A^-1 B) * (B^T A^-1 B)^-1 * (A^-1 B)^T
    
    D = (A^-1 B) (B^T A^-1 B)^-1
    
    E = - (B^T A^-1 B)^-1
  

  The inverse of `A` is not computed, but instead its action is
  performed by solving the Cholesky decomposition of `A`. `A` can be a
  scipy sparse matrix or a numpy array. `B` can also be either a scipy
  sparse matrix or a numpy array but it will be converted to a numpy
  array.
   
  Note
  ----
  This class stores the factorization of `A`, which may be sparse, the
  dense matrix `A^-1 B`, and the dense factorization of `B^T A^-1 B`.
  If the number of columns in `B` is large then this may take up too
  much memory.
  '''
  def __init__(self, A, B):
    ''' 
    Parameters
    ----------
    A : (N, N) array or sparse matrix
    B : (N, P) array or sparse matrix
    '''
    # make sure A is either a csc sparse matrix or a float array
    A = as_sparse_or_array(A, dtype=float)
    
    # convert B to dense if it is sparse
    B = as_array(B, dtype=float)
    n, p = B.shape
    if n < p:
      raise np.linalg.LinAlgError(
        'There are fewer rows than columns in `B`. This makes the '
        'block matrix singular, and its inverse cannot be computed.')
    
    A_solver = PosDefSolver(A)
    AiB = A_solver.solve(B) 
    BtAiB_solver = PosDefSolver(B.T.dot(AiB))
    self._AiB = AiB
    self._A_solver = A_solver
    self._BtAiB_solver = BtAiB_solver 
    
  def solve(self, a, b):   
    ''' 
    Solves for `x` and `y` given `a` and `b`.
    
    Parameters
    ----------
    a : (N, *) array or sparse matrix
    b : (P, *) array or sparse matrix
    
    Returns
    -------
    (N, *) array
    (P, *) array
    '''
    a = as_array(a, dtype=float)
    b = as_array(b, dtype=float)
    Eb  = -self._BtAiB_solver.solve(b)
    Db  = -self._AiB.dot(Eb)
    Dta = self._BtAiB_solver.solve(self._AiB.T.dot(a))
    Ca  = self._A_solver.solve(a) - self._AiB.dot(Dta)
    x = Ca  + Db    
    y = Dta + Eb
    return x, y
