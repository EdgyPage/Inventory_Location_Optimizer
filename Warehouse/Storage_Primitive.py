class Storage_Size:

    def __init__(self):
        self.available_sizes_heights = {
            'small': 12,
            'medium': 24,
            'large': 36,
            'extra_large': 48
        }
        self.max_length = 48
        self.max_width = 48

class Storage_Type:
    def __init__(self):
        self.available_storage_types = ['conveyable', 'non-conveyable']
        self.available_sub_storage_types = ['food', 'clothing', 'electronic', 
                                            'furniture', 'seasonal', 'chemical']
    