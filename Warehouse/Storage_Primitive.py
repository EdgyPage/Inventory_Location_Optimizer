import itertools

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
        self.handling_storage_types = ['conveyable', 'non-conveyable']
        self.category_storage_types = ['food', 'clothing', 'electronic', 
                                            'furniture', 'seasonal', 'chemical']
        self.available_storage_types = list(itertools.product(self.handling_storage_types, self.category_storage_types))