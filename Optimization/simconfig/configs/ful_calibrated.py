"""Fulfillment channel — base human-walker pick-config (FulfillmentCart, one-way lanes).

The FIRST/only fulfillment entry is the single source of truth for the default walker cost —
channels.fulfillment_pick_config() reads it back via sim_config.FULFILLMENT_CONFIGS[0].  Keep the
name DISTINCT from any store config name (config.json is written per config dir; a shared name
would collide — see the runner's _prepare_channel_run).
"""
from Optimization.simconfig.core.registry import pick_config
from Optimization.simconfig.constants import _FF_PICKERS


@pick_config(name='ful_calibrated', channel='fulfillment', order=0)
def ful_calibrated():
    return {
        'name'            : 'ful_calibrated',
        'pick_intercept'  : 10,
        'pick_weight_coef': 0.7,
        'pick_weight_fn'  : 'log',
        'pick_volume_coef': 0.09,
        'pick_volume_fn'  : 'log',
        'cart_swap_coef'  : 240,
        'cart'            : 'FulfillmentCart',
        'x_speed'         : 2,    # ft/s
        'y_speed'         : 4,    # ft/s
        'one_way'         : True, # one-way lanes: aisle DEPTH drives x-travel (see travel model)
        'num_pickers'     : _FF_PICKERS,   # walker pool size (independent of store pickers)
        # height_brackets omitted → DEFAULT (no-op for ff bins, all M=1).
    }
