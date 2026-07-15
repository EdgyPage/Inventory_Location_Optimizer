"""Store variant — base weight, steeper height brackets (menu entry, not in the active sweep).
Registered with enabled=False; flip enabled=True to add it to the store sweep."""
from Optimization.simconfig.core.registry import pick_config
from Optimization.simconfig.constants import _STORE_PICKERS


@pick_config(name='store_high_height', channel='store', order=11, enabled=False)
def store_high_height():
    return {
        'name'            : 'store_high_height',
        'pick_intercept'  : 15,
        'pick_weight_coef': 0.58,
        'pick_weight_fn'  : 'pow:1.5',
        'pick_volume_coef': 0.7,
        'pick_volume_fn'  : 'log:2',
        'cart_swap_coef'  : 300,
        'x_speed'         : 3,    # ft/s
        'y_speed'         : 2,    # ft/s
        'num_pickers'     : _STORE_PICKERS,
        'height_brackets' : ((96.0, 1.0), (240.0, 1.4), (float('inf'), 1.8)),
    }
