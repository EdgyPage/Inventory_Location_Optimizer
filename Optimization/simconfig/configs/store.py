"""Store channel — base ergonomics pick-config (StoreCart). The single active store calibration."""
from Optimization.simconfig.core.registry import pick_config
from Optimization.simconfig.constants import _STORE_PICKERS


@pick_config(name='store', channel='store', order=0)
def store():
    return {
        'name'            : 'store',
        'pick_intercept'  : 15,
        'pick_weight_coef': 0.58,
        'pick_weight_fn'  : 'pow:1.5',
        'pick_volume_coef': 0.7,
        'pick_volume_fn'  : 'log:2',
        'cart_swap_coef'  : 300,
        'x_speed'         : 3,    # ft/s
        'y_speed'         : 2,    # ft/s
        'num_pickers'     : _STORE_PICKERS,   # machine order-picker pool size
        'height_brackets' : ((96.0, 1.0), (240.0, 1.2), (float('inf'), 1.4)),
    }
