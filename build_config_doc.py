from auto_pumping.config import Config
import jsonschema2md


schema = Config.model_json_schema()

parser = jsonschema2md.Parser(
    examples_as_yaml=True,
    show_examples="all",
)

markdown = "".join(parser.parse_schema(schema))

with open("docs/config.md", "w") as f:
    f.write(markdown)
