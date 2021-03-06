import ckan.plugins as p

from ckan.lib.base import BaseController, render, c
import ckan.model as model
from pylons import request, response
import ckan.lib.dictization.model_dictize as model_dictize
import json, re
import logging
from jsonschema.exceptions import best_match
import StringIO

try:
    from collections import OrderedDict  # 2.7
except ImportError:
    from sqlalchemy.util import OrderedDict

logger = logging.getLogger('datajson')


def get_validator():
    import os
    from jsonschema import Draft4Validator, FormatChecker

    #schema_path = os.path.join(os.path.dirname(__file__), 'schema', 'federal-v1.1', 'dataset.json')
    schema_path = os.path.join(os.path.dirname(__file__), 'schema', 'nonfederal-v1.1', 'dataset.json')
    with open(schema_path, 'r') as file:
        schema = json.loads(file.read())
        return Draft4Validator(schema, format_checker=FormatChecker())

    logger.warn('Unable to create validator')
    return None


validator = get_validator()

try:
    from collections import OrderedDict  # 2.7
except ImportError:
    from sqlalchemy.util import OrderedDict

from build_datajson import make_datajson_entry, make_datajson_catalog

# from build_enterprisedatajson import make_enterprisedatajson_entry
from build_datajsonld import dataset_to_jsonld


class DataJsonPlugin(p.SingletonPlugin):
    p.implements(p.interfaces.IConfigurer)
    p.implements(p.interfaces.IRoutes, inherit=True)

    def update_config(self, config):
        # Must use IConfigurer rather than IConfigurable because only IConfigurer
        # is called before after_map, in which we need the configuration directives
        # to know how to set the paths.

        # TODO commenting out enterprise data inventory for right now
        # DataJsonPlugin.route_edata_path = config.get("ckanext.enterprisedatajson.path", "/enterprisedata.json")
        DataJsonPlugin.route_enabled = config.get("ckanext.datajson.url_enabled", "True") == 'True'
        DataJsonPlugin.route_path = config.get("ckanext.datajson.path", "/data.json")
        DataJsonPlugin.route_ld_path = config.get("ckanext.datajsonld.path",
                                                  re.sub(r"\.json$", ".jsonld", DataJsonPlugin.route_path))
        DataJsonPlugin.ld_id = config.get("ckanext.datajsonld.id", config.get("ckan.site_url"))
        DataJsonPlugin.ld_title = config.get("ckan.site_title", "Catalog")
        DataJsonPlugin.site_url = config.get("ckan.site_url")

        # Adds our local templates directory. It's smart. It knows it's
        # relative to the path of *this* file. Wow.
        p.toolkit.add_template_directory(config, "templates")

    def before_map(self, m):
        return m

    def after_map(self, m):
        if DataJsonPlugin.route_enabled:
            # /data.json and /data.jsonld (or other path as configured by user)
            m.connect('datajson', DataJsonPlugin.route_path, controller='ckanext.datajson.plugin:DataJsonController',
                      action='generate_json')
            # TODO commenting out enterprise data inventory for right now
            # m.connect('enterprisedatajson', DataJsonPlugin.route_edata_path, controller='ckanext.datajson.plugin:DataJsonController', action='generate_enterprise')
            #m.connect('datajsonld', DataJsonPlugin.route_ld_path, controller='ckanext.datajson.plugin:DataJsonController', action='generate_jsonld')

        # TODO DWC update action
        # /data/{org}/data.json
        m.connect('public_data_listing', '/organization/{id}/data.json',
                  controller='ckanext.datajson.plugin:DataJsonController', action='generate_pdl')

        # TODO DWC update action
        # /data/{org}/edi.json
        m.connect('enterprise_data_inventory', '/organization/{id}/edi.json',
                  controller='ckanext.datajson.plugin:DataJsonController', action='generate_edi')

        # /pod/validate
        # m.connect('datajsonvalidator', "/pod/validate", controller='ckanext.datajson.plugin:DataJsonController', action='validator')

        return m


class DataJsonController(BaseController):
    def generate_output(self, format):
        # set content type (charset required or pylons throws an error)
        response.content_type = 'application/json; charset=UTF-8'

        # allow caching of response (e.g. by Apache)
        del response.headers["Cache-Control"]
        del response.headers["Pragma"]

        # TODO special processing for enterprise
        # output
        data = make_json()

        if format == 'json-ld':
            # Convert this to JSON-LD.
            data = OrderedDict([
                ("@context", OrderedDict([
                    ("rdfs", "http://www.w3.org/2000/01/rdf-schema#"),
                    ("dcterms", "http://purl.org/dc/terms/"),
                    ("dcat", "http://www.w3.org/ns/dcat#"),
                    ("foaf", "http://xmlns.com/foaf/0.1/"),
                ])
                ),
                ("@id", DataJsonPlugin.ld_id),
                ("@type", "dcat:Catalog"),
                ("dcterms:title", DataJsonPlugin.ld_title),
                ("rdfs:label", DataJsonPlugin.ld_title),
                ("foaf:homepage", DataJsonPlugin.site_url),
                ("dcat:dataset", [dataset_to_jsonld(d) for d in data]),
            ])
        elif format == 'json':
            data = make_datajson_catalog(data)

        return p.toolkit.literal(json.dumps(data))

    def generate_json(self):
        return self.generate_output('json')

    def generate_jsonld(self):
        return self.generate_output('json-ld')

    def validator(self):
        # Validates that a URL is a good data.json file.
        if request.method == "POST" and "url" in request.POST and request.POST["url"].strip() != "":
            c.source_url = request.POST["url"]
            c.errors = []

            import urllib, json
            from datajsonvalidator import do_validation

            body = None
            try:
                body = json.load(urllib.urlopen(c.source_url))
            except IOError as e:
                c.errors.append(("Error Loading File", ["The address could not be loaded: " + unicode(e)]))
            except ValueError as e:
                c.errors.append(("Invalid JSON", ["The file does not meet basic JSON syntax requirements: " + unicode(
                    e) + ". Try using JSONLint.com."]))
            except Exception as e:
                c.errors.append((
                "Internal Error", ["Something bad happened while trying to load and parse the file: " + unicode(e)]))

            if body:
                try:
                    do_validation(body, c.errors)
                except Exception as e:
                    c.errors.append(("Internal Error", ["Something bad happened: " + unicode(e)]))
                if len(c.errors) == 0:
                    c.errors.append(("No Errors", ["Great job!"]))

        return render('datajsonvalidator.html')

    def generate_pdl(self, id):
        # set content type (charset required or pylons throws an error)
        response.content_type = 'application/json; charset=UTF-8'

        # allow caching of response (e.g. by Apache)
        del response.headers["Cache-Control"]
        del response.headers["Pragma"]
        return make_pdl(id)

    def generate_edi(self, id):
        # set content type (charset required or pylons throws an error)
        response.content_type = 'application/json; charset=UTF-8'

        # allow caching of response (e.g. by Apache)
        del response.headers["Cache-Control"]
        del response.headers["Pragma"]
        return make_edi(id)


def make_json():
    # Build the data.json file.
    limit = 1000
    offset = 0
    packages = []
    while True:
        curr_packages = p.toolkit.get_action("current_package_list_with_resources")(None, {'limit':limit, 'offset':offset})
        if curr_packages:
            packages.extend(curr_packages)
            offset += limit
        else:
            break
    output = []
    # Create data.json only using public and public-restricted datasets, datasets marked non-public are not exposed
    for pkg in packages:
        try:
            if not pkg['private']:
                datajson_entry = make_datajson_entry(pkg)
                if datajson_entry:
                    output.append(datajson_entry)
                else:
                    logger.warn("Dataset id=[%s], title=[%s] omitted\n", pkg.get('id', None), pkg.get('title', None))
        except KeyError:
            logger.warn("Dataset id=[%s], title=[%s] missing required 'public_access_level' field", pkg.get('id', None),
                        pkg.get('title', None))
            pass
    return output


def make_edi(org_id):
    # Error handler for creating error log
    stream = StringIO.StringIO()
    eh = logging.StreamHandler(stream)
    eh.setLevel(logging.WARN)
    formatter = logging.Formatter('%(asctime)s - %(message)s')
    eh.setFormatter(formatter)
    logger.addHandler(eh)

    # Build the data.json file.
    packages = get_all_group_packages(group_id=org_id)
    output = []
    for pkg in packages:
        if pkg['owner_org'] == org_id or pkg.get('organization',{}).get('name') == org_id:
            datajson_entry = make_datajson_entry(pkg)
            if datajson_entry and is_valid(datajson_entry):
                output.append(datajson_entry)
            else:
                logger.warn("Dataset id=[%s], title=[%s] omitted\n", pkg.get('id', None), pkg.get('title', None))

    # Get the error log
    eh.flush()
    error = stream.getvalue()
    eh.close()
    logger.removeHandler(eh)
    stream.close()

    #return json.dumps(output)
    return write_zip(output, error, zip_name='edi')


def make_pdl(org_id):
    # Error handler for creating error log
    stream = StringIO.StringIO()
    eh = logging.StreamHandler(stream)
    eh.setLevel(logging.WARN)
    formatter = logging.Formatter('%(asctime)s - %(message)s')
    eh.setFormatter(formatter)
    logger.addHandler(eh)


    # Build the data.json file.
    packages = get_all_group_packages(group_id=org_id)

    datasets = []
    #Create data.json only using public datasets, datasets marked non-public are not exposed
    for pkg in packages:
        try:
            if ((pkg['owner_org'] == org_id or pkg.get('organization',{}).get('name') == org_id) and
            pkg['type'] == 'dataset' and not pkg['private']):
                datajson_entry = make_datajson_entry(pkg)
                if datajson_entry and is_valid(datajson_entry):
                    datasets.append(datajson_entry)
                else:
                    logger.warn("Dataset id=[%s], title=[%s] omitted\n", pkg.get('id', None), pkg.get('title', None))

        except KeyError:
            logger.warn("Dataset id=[%s], title=['%s'] missing required field",
                        pkg.get('id', None), pkg.get('title', None))
            pass

    output = OrderedDict([
        ('conformsTo', 'https://project-open-data.cio.gov/v1.1/schema'),
        ('describedBy', 'https://project-open-data.cio.gov/v1.1/schema/catalog.json'),
        ('@context', 'https://project-open-data.cio.gov/v1.1/schema/catalog.jsonld'),
        ('@type', 'dcat:Catalog'),
        ('dataset', datasets),
    ])

    # Get the error log
    eh.flush()
    error = stream.getvalue()
    eh.close()
    logger.removeHandler(eh)
    stream.close()

    return json.dumps(output)


def get_all_group_packages(group_id):
    """
    Gets all of the group packages, public or private, returning them as a list of CKAN's dictized packages.
    """
    result = []
    for pkg_rev in model.Group.get(group_id).packages(with_private=True, context={'user_is_admin': True}):
        result.append(model_dictize.package_dictize(pkg_rev, {'model': model}))

    return result


def is_valid(instance):
    """
    Validates a data.json entry against the project open data's JSON schema. Log a warning message on validation error
    """
    error = best_match(validator.iter_errors(instance))
    if error:
        logger.warn("Validation failed, best guess of error = %s", error)
        return False
    return True


def write_zip(data, error=None, zip_name='data'):
    """
    Data: a python object to write to the data.json
    Error: unicode string representing the content of the error log.
    zip_name: the name to use for the zip file
    """
    import zipfile

    o = StringIO.StringIO()
    zf = zipfile.ZipFile(o, mode='w')

    # Write the data file
    if data:
        zf.writestr('data.json', json.dumps(make_datajson_catalog(data), ensure_ascii=False).encode('utf8'))

    #Write the error log
    if error:
        zf.writestr('errorlog.txt', error.encode('utf8'))

    zf.close()
    o.seek(0)

    binary = o.read()
    o.close()

    response.content_type = 'application/octet-stream'
    response.content_disposition = 'attachment; filename="%s.zip"' % zip_name

    return binary
