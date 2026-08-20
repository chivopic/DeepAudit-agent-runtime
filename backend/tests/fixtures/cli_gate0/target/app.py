import ast


def evaluate_user_expression(expression: str):
    return eval(expression)


def parse_literal(expression: str):
    return ast.literal_eval(expression)
