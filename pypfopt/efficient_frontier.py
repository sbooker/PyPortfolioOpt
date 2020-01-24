"""
The ``efficient_frontier`` module houses the EfficientFrontier class, which
generates optimal portfolios for various possible objective functions and parameters.
"""

import warnings
import numpy as np
import pandas as pd
import scipy.optimize as sco
from . import objective_functions, base_optimizer


class EfficientFrontier(base_optimizer.BaseScipyOptimizer):

    """
    An EfficientFrontier object (inheriting from BaseScipyOptimizer) contains multiple
    optimisation methods that can be called (corresponding to different objective
    functions) with various parameters.

    Instance variables:

    - Inputs:

        - ``n_assets`` - int
        - ``tickers`` - str list
        - ``bounds`` - float tuple OR (float tuple) list
        - ``cov_matrix`` - pd.DataFrame
        - ``expected_returns`` - pd.Series

    - Optimisation parameters:

        - ``initial_guess`` - np.ndarray
        - ``constraints`` - dict list
        - ``opt_method`` - the optimisation algorithm to use. Defaults to SLSQP.

    - Output: ``weights`` - np.ndarray

    Public methods:

    - ``max_sharpe()`` optimises for maximal Sharpe ratio (a.k.a the tangency portfolio)
    - ``min_volatility()`` optimises for minimum volatility
    - ``custom_objective()`` optimises for some custom objective function
    - ``efficient_risk()`` maximises Sharpe for a given target risk
    - ``efficient_return()`` minimises risk for a given target return
    - ``portfolio_performance()`` calculates the expected return, volatility and Sharpe ratio for
      the optimised portfolio.
    - ``set_weights()`` creates self.weights (np.ndarray) from a weights dict
    - ``clean_weights()`` rounds the weights and clips near-zeros.
    - ``save_weights_to_file()`` saves the weights to csv, json, or txt.
    """

    def __init__(self, expected_returns, cov_matrix, weight_bounds=(0, 1), gamma=0):
        """
        :param expected_returns: expected returns for each asset. Set to None if
                                 optimising for volatility only.
        :type expected_returns: pd.Series, list, np.ndarray
        :param cov_matrix: covariance of returns for each asset
        :type cov_matrix: pd.DataFrame or np.array
        :param weight_bounds: minimum and maximum weight of each asset OR single min/max pair
                              if all identical, defaults to (0, 1). Must be changed to (-1, 1)
                              for portfolios with shorting.
        :type weight_bounds: tuple OR tuple list, optional
        :param gamma: L2 regularisation parameter, defaults to 0. Increase if you want more
                      non-negligible weights
        :type gamma: float, optional
        :raises TypeError: if ``expected_returns`` is not a series, list or array
        :raises TypeError: if ``cov_matrix`` is not a dataframe or array
        """
        # Inputs
        self.cov_matrix = cov_matrix
        if expected_returns is not None:
            if not isinstance(expected_returns, (pd.Series, list, np.ndarray)):
                raise TypeError("expected_returns is not a series, list or array")
            if not isinstance(cov_matrix, (pd.DataFrame, np.ndarray)):
                raise TypeError("cov_matrix is not a dataframe or array")
            self.expected_returns = expected_returns
        if isinstance(expected_returns, pd.Series):
            tickers = list(expected_returns.index)
        elif isinstance(cov_matrix, pd.DataFrame):
            tickers = list(cov_matrix.columns)
        else:
            tickers = list(range(len(expected_returns)))

        super().__init__(len(tickers), tickers, weight_bounds)

        if not isinstance(gamma, (int, float)):
            raise ValueError("gamma should be numeric")
        if gamma < 0:
            warnings.warn("in most cases, gamma should be positive", UserWarning)
        self.gamma = gamma

    def max_sharpe(self, risk_free_rate=0.02):
        """
        Maximise the Sharpe Ratio. The result is also referred to as the tangency portfolio,
        as it is the tangent to the efficient frontier curve that intercepts the risk-free
        rate.

        :param risk_free_rate: risk-free rate of borrowing/lending, defaults to 0.02.
                               The period of the risk-free rate should correspond to the
                               frequency of expected returns.
        :type risk_free_rate: float, optional
        :raises ValueError: if ``risk_free_rate`` is non-numeric
        :return: asset weights for the Sharpe-maximising portfolio
        :rtype: dict
        """
        if not isinstance(risk_free_rate, (int, float)):
            raise ValueError("risk_free_rate should be numeric")

        args = (self.expected_returns, self.cov_matrix, self.gamma, risk_free_rate)
        result = sco.minimize(
            objective_functions.negative_sharpe,
            x0=self.initial_guess,
            args=args,
            method=self.opt_method,
            bounds=self.bounds,
            constraints=self.constraints,
        )
        self.weights = result["x"]
        return dict(zip(self.tickers, self.weights))

    def min_volatility(self):
        """
        Minimise volatility.

        :return: asset weights for the volatility-minimising portfolio
        :rtype: dict
        """
        args = (self.cov_matrix, self.gamma)
        result = sco.minimize(
            objective_functions.volatility,
            x0=self.initial_guess,
            args=args,
            method=self.opt_method,
            bounds=self.bounds,
            constraints=self.constraints,
        )
        self.weights = result["x"]
        return dict(zip(self.tickers, self.weights))

    def max_unconstrained_utility(self, risk_aversion=1):
        r"""
        Solve for weights in the unconstrained maximisation problem:

        .. math::

            \max_w w^T \mu - \frac \delta 2 w^T \Sigma w

        This has an analytic solution, so scipy.optimize is not needed.
        Note: this method ignores most of the parameters passed in the
        constructor, including bounds and gamma. Because this is unconstrained,
        resulting weights may be negative or greater than 1. It is completely up
        to the user to decide how the resulting weights should be normalised.

        :param risk_aversion: risk aversion parameter (must be greater than 0),
                              defaults to 1
        :type risk_aversion: positive float
        """
        if risk_aversion <= 0:
            raise ValueError("risk aversion coefficient must be greater than zero")
        A = risk_aversion * self.cov_matrix
        b = self.expected_returns
        self.weights = np.linalg.solve(A, b)
        return dict(zip(self.tickers, self.weights))

    def custom_objective(self, objective_function, *args):
        """
        Optimise some objective function. While an implicit requirement is that the function
        can be optimised via a quadratic optimiser, this is not enforced. Thus there is a
        decent chance of silent failure.

        :param objective_function: function which maps (weight, args) -> cost
        :type objective_function: function with signature (np.ndarray, args) -> float
        :return: asset weights that optimise the custom objective
        :rtype: dict
        """
        result = sco.minimize(
            objective_function,
            x0=self.initial_guess,
            args=args,
            method=self.opt_method,
            bounds=self.bounds,
            constraints=self.constraints,
        )
        self.weights = result["x"]
        return dict(zip(self.tickers, self.weights))

    def efficient_risk(self, target_risk, risk_free_rate=0.02, market_neutral=False):
        """
        Calculate the Sharpe-maximising portfolio for a given volatility (i.e max return
        for a target risk).

        :param target_risk: the desired volatility of the resulting portfolio.
        :type target_risk: float
        :param risk_free_rate: risk-free rate of borrowing/lending, defaults to 0.02.
                               The period of the risk-free rate should correspond to the
                               frequency of expected returns.
        :type risk_free_rate: float, optional
        :param market_neutral: whether the portfolio should be market neutral (weights sum to zero),
                               defaults to False. Requires negative lower weight bound.
        :param market_neutral: bool, optional
        :raises ValueError: if ``target_risk`` is not a positive float
        :raises ValueError: if no portfolio can be found with volatility equal to ``target_risk``
        :raises ValueError: if ``risk_free_rate`` is non-numeric
        :return: asset weights for the efficient risk portfolio
        :rtype: dict
        """
        if not isinstance(target_risk, float) or target_risk < 0:
            raise ValueError("target_risk should be a positive float")
        if not isinstance(risk_free_rate, (int, float)):
            raise ValueError("risk_free_rate should be numeric")

        args = (self.expected_returns)
        target_constraint = {
            "type": "eq",
            "fun": lambda w: target_risk ** 2
            - objective_functions.volatility(w, self.cov_matrix),
        }
        # The equality constraint is either "weights sum to 1" (default), or
        # "weights sum to 0" (market neutral).
        if market_neutral:
            portfolio_possible = any(b[0] < 0 for b in self.bounds if b[0] is not None)
            if not portfolio_possible:
                warnings.warn(
                    "Market neutrality requires shorting - bounds have been amended",
                    RuntimeWarning,
                )
                self.bounds = self._make_valid_bounds((-1, 1))
            constraints = [
                {"type": "eq", "fun": lambda x: np.sum(x)},
                target_constraint,
            ]
        else:
            constraints = self.constraints + [target_constraint]

        result = sco.minimize(
            objective_functions.negative_mean_return,
            x0=self.initial_guess,
            args=args,
            method=self.opt_method,
            bounds=self.bounds,
            constraints=constraints,
        )
        self.weights = result["x"]

        if not np.isclose(
            objective_functions.volatility(self.weights, self.cov_matrix),
            target_risk ** 2,
        ):
            raise ValueError(
                "Optimisation was not succesful. Please increase target_risk"
            )

        return dict(zip(self.tickers, self.weights))

    def efficient_return(self, target_return, market_neutral=False):
        """
        Calculate the 'Markowitz portfolio', minimising volatility for a given target return.

        :param target_return: the desired return of the resulting portfolio.
        :type target_return: float
        :param market_neutral: whether the portfolio should be market neutral (weights sum to zero),
                               defaults to False. Requires negative lower weight bound.
        :type market_neutral: bool, optional
        :raises ValueError: if ``target_return`` is not a positive float
        :raises ValueError: if no portfolio can be found with return equal to ``target_return``
        :return: asset weights for the Markowitz portfolio
        :rtype: dict
        """
        if not isinstance(target_return, float) or target_return < 0:
            raise ValueError("target_return should be a positive float")

        args = (self.cov_matrix, self.gamma)
        target_constraint = {
            "type": "eq",
            "fun": lambda w: w.dot(self.expected_returns) - target_return,
        }
        # The equality constraint is either "weights sum to 1" (default), or
        # "weights sum to 0" (market neutral).
        if market_neutral:
            portfolio_possible = any(b[0] < 0 for b in self.bounds if b[0] is not None)
            if not portfolio_possible:
                warnings.warn(
                    "Market neutrality requires shorting - bounds have been amended",
                    RuntimeWarning,
                )
                self.bounds = self._make_valid_bounds((-1, 1))
            constraints = [
                {"type": "eq", "fun": lambda x: np.sum(x)},
                target_constraint,
            ]
        else:
            constraints = self.constraints + [target_constraint]

        result = sco.minimize(
            objective_functions.volatility,
            x0=self.initial_guess,
            args=args,
            method=self.opt_method,
            bounds=self.bounds,
            constraints=constraints,
        )
        self.weights = result["x"]
        if not np.isclose(self.weights.dot(self.expected_returns), target_return):
            raise ValueError(
                "Optimisation was not succesful. Please reduce target_return"
            )
        return dict(zip(self.tickers, self.weights))

    def portfolio_performance(self, verbose=False, risk_free_rate=0.02):
        """
        After optimising, calculate (and optionally print) the performance of the optimal
        portfolio. Currently calculates expected return, volatility, and the Sharpe ratio.

        :param verbose: whether performance should be printed, defaults to False
        :type verbose: bool, optional
        :param risk_free_rate: risk-free rate of borrowing/lending, defaults to 0.02.
                               The period of the risk-free rate should correspond to the
                               frequency of expected returns.
        :type risk_free_rate: float, optional
        :raises ValueError: if weights have not been calcualted yet
        :return: expected return, volatility, Sharpe ratio.
        :rtype: (float, float, float)
        """
        return base_optimizer.portfolio_performance(
            self.expected_returns,
            self.cov_matrix,
            self.weights,
            verbose,
            risk_free_rate,
        )
