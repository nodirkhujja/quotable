import yaml

def yaml_coerce(value):
    # Convert value to proper Python

    if isinstance(value, str):
        # yaml returns python object
        # Converts string dict "{'apples': 1, 'bacon': 2}" to python dict
        # Useful becouse sometimes we need stringify settings this way (like in Dockerfile)
        return yaml.load(f'dummy: {value}', Loader=yaml.SafeLoader)['dummy']
    
    return value