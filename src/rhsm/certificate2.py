#
# Copyright (c) 2012 Red Hat, Inc.
#
# This software is licensed to you under the GNU General Public License,
# version 2 (GPLv2). There is NO WARRANTY for this software, express or
# implied, including the implied warranties of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. You should have received a copy of GPLv2
# along with this software; if not, see
# http://www.gnu.org/licenses/old-licenses/gpl-2.0.txt.
#
# Red Hat trademarks are not licensed under GPLv2. No permission is
# granted to use or replicate Red Hat trademarks that are incorporated
# in this software or its documentation.
#

from datetime import datetime

from M2Crypto import X509

from rhsm.connection import safe_int
from rhsm.certificate import Extensions, OID, DateRange, GMT, \
        get_datetime_from_x509, parse_tags

REDHAT_OID_NAMESPACE = "1.3.6.1.4.1.2312.9"
ORDER_NAMESPACE = "4"

EXT_ORDER_NAME = "4.1"
EXT_CERT_VERSION = "6"

# Constants representing the type of certificates:
PRODUCT_CERT = 1
ENTITLEMENT_CERT = 2
IDENTITY_CERT = 3

class CertFactory(object):
    """
    Factory for creating certificate objects.

    Examines the incoming file or PEM text, parses the OID structure,
    determines the type of certificate we're dealing with
    (entitlement/product), as well as the version of the certificate
    from the server, and returns the correct implementation class.
    """

    def create_from_file(self, path):
        """
        Create appropriate certificate object from a PEM file on disk.
        """
        f = open(path)
        contents = f.read()
        f.close()
        return self.create_from_pem(contents, path=path)

    def create_from_pem(self, pem, path=None):
        """
        Create appropriate certificate object from a PEM string.
        """
        # Load the X509 extensions so we can determine what we're dealing with:
        x509 = X509.load_cert_string(pem)
        extensions = Extensions(x509)
        redhat_oid = OID(REDHAT_OID_NAMESPACE)
        # Trim down to only the extensions in the Red Hat namespace:
        extensions = extensions.ltrim(len(redhat_oid))

        # Check the certificate version, absence of the extension implies v1.0:
        cert_version_str = "1.0"
        if EXT_CERT_VERSION in extensions:
            cert_version_str = extensions[EXT_CERT_VERSION]

        version = Version(cert_version_str)
        if version.major == 1:
            return self.__create_v1_cert(version, extensions, x509, path)
        return cert

    def __create_v1_cert(self, version, extensions, x509, path):

        cert_type = self._get_cert_type(extensions)

        if cert_type == IDENTITY_CERT:
            return self.__create_identity_cert(extensions, x509, path)
        elif cert_type == ENTITLEMENT_CERT:
            return self.__create_v1_ent_cert(version, extensions, x509, path)
        elif cert_type == PRODUCT_CERT:
            return self.__create_v1_prod_cert(version, extensions, x509, path)
#        cert_class = VERSION_IMPLEMENTATIONS[version.major] \
#                [self._get_cert_type(extensions)]

    def __read_alt_name(self, x509):
        alt_name = None
        try:
            name_ext = x509.get_ext('subjectAltName')
            if name_ext:
                alt_name = name_ext.get_value()
        except LookupError:
            # This may not be defined, seems to only be used for identity
            # certificates:
            pass
        return alt_name

    def __read_subject(self, x509):
        subj = {}
        subject = x509.get_subject()
        subject.nid['UID'] = 458
        for key, nid in subject.nid.items():
            entry = subject.get_entries_by_nid(nid)
            if len(entry):
                asn1 = entry[0].get_data()
                subj[key] = str(asn1)
                continue
        return subj

    def __create_identity_cert(self, extensions, x509, path):
        cert = IdentityCertificate(
                x509=x509,
                path=path,
                serial=x509.get_serial_number(),
                start=get_datetime_from_x509(x509.get_not_before()),
                end=get_datetime_from_x509(x509.get_not_after()),
                alt_name=self.__read_alt_name(x509),
                subject=self.__read_subject(x509),
            )
        return cert

    def __create_v1_prod_cert(self, version, extensions, x509, path):
        products = self.__parse_v1_products(extensions)
        cert = ProductCertificate1(
                x509=x509,
                path=path,
                version=version,
                serial=x509.get_serial_number(),
                start=get_datetime_from_x509(x509.get_not_before()),
                end=get_datetime_from_x509(x509.get_not_after()),
                products=products,
            )
        return cert

    def __create_v1_ent_cert(self, version, extensions, x509, path):
        order = self.__parse_v1_order(extensions)
        content = self.__parse_v1_content(extensions)
        products = self.__parse_v1_products(extensions)

        cert = EntitlementCertificate1(
                x509=x509,
                path=path,
                version=version,
                serial=x509.get_serial_number(),
                start=get_datetime_from_x509(x509.get_not_before()),
                end=get_datetime_from_x509(x509.get_not_after()),
                order=order,
                content=content,
                products=products,
            )
        return cert

    def __parse_v1_products(self, extensions):
        """
        Returns an ordered list of all the product data in the
        certificate.
        """
        products = []
        for prod_namespace in extensions.find('1.*.1'):
            oid = prod_namespace[0]
            root = oid.rtrim(1)
            product_id = oid[1]
            ext = extensions.branch(root)
            products.append(Product(
                id=product_id,
                name=ext.get('1'),
                version=ext.get('2'),
                arch=ext.get('3'),
                provided_tags=parse_tags(ext.get('4')),
                ))
        return products

    def __parse_v1_order(self, extensions):
        order_extensions = extensions.branch(ORDER_NAMESPACE)
        order = Order(
                name=order_extensions.get('1'),
                number=order_extensions.get('2'),
                sku=order_extensions.get('3'),
                subscription=order_extensions.get('4'),
                quantity=safe_int(order_extensions.get('5')),
                virt_limit=order_extensions.get('8'),
                socket_limit=order_extensions.get('9'),
                contract_number=order_extensions.get('10'),
                quantity_used=order_extensions.get('11'),
                warning_period=order_extensions.get('12'),
                account_number=order_extensions.get('13'),
                provides_management=order_extensions.get('14'),
                support_level=order_extensions.get('15'),
                support_type=order_extensions.get('16'),
                stacking_id=order_extensions.get('17'),
                virt_only=order_extensions.get('18')
            )
        return order

    def __parse_v1_content(self, extensions):
        content = []
        ents = extensions.find("2.*.1.1")
        for ent in ents:
            oid = ent[0]
            content_ext = extensions.branch(oid.rtrim(1))
            content.append(Content(
                name=content_ext.get('1'),
                label=content_ext.get('2'),
                quantity=content_ext.get('3'),
                flex_quantity=content_ext.get('4'),
                vendor=content_ext.get('5'),
                url=content_ext.get('6'),
                gpg=content_ext.get('7'),
                enabled=content_ext.get('8'),
                metadata_expire=content_ext.get('9'),
                required_tags=parse_tags(content_ext.get('10')),
            ))
        return content

    def _get_cert_type(self, extensions):
        if len(extensions) == 0:
            return IDENTITY_CERT
        # Assume if there is an order name, it must be an entitlement cert:
        elif EXT_ORDER_NAME in extensions:
            return ENTITLEMENT_CERT
        else:
            return PRODUCT_CERT
        # TODO: as soon as we have a v2 cert to play with, we need to look
        # for the new json OID, decompress it, parse it, and then look for an
        # order namespace in that as well.


class Version(object):
    """ Small wrapper for version string comparisons. """
    def __init__(self, version_str):
        self.version_str = version_str
        self.segments = version_str.split(".")
        for i in range(len(self.segments)):
            self.segments[i] = int(self.segments[i])

        self.major = self.segments[0]
        self.minor = 0
        if len(self.segments) > 1:
            self.minor = self.segments[1]

    # TODO: comparator might be useful someday
    def __str__(self):
        return self.version_str


class Certificate(object):
    """ Parent class of all x509 certificate types. """
    def __init__(self, x509=None, path=None, version=None, serial=None, start=None,
            end=None):

        # The X509 M2crypto object for this certificate:
        # TODO: this shouldn't be here, makes it hard to create the object.
        # More writing somewhere else.
        self.x509 = x509

        # Full file path to the certificate on disk. May be None if the cert
        # hasn't yet been written to disk.
        self.path = path

        # Version of the certificate sent by Candlepin:
        self.version = version

        self.serial = serial

        # Certificate start/end datetimes:
        self.start = start
        self.end = end

        self.valid_range = DateRange(self.start, self.end)

    def is_valid(self, on_date=None):
        gmt = datetime.utcnow()
        if on_date:
            gmt = on_date
        gmt = gmt.replace(tzinfo=GMT())
        return self.valid_range.has_date(gmt)

    def is_expired(self, on_date=None):
        gmt = datetime.utcnow()
        if on_date:
            gmt = on_date
        gmt = gmt.replace(tzinfo=GMT())
        return self.valid_range.end() < gmt

    def __cmp__(self, other):
        if self.end < other.end:
            return -1
        if self.end > other.end:
            return 1
        return 0

    def write(self, path):
        """
        Write the certificate to disk.
        """
        f = open(path, 'w')
        f.write(self.x509.as_pem())
        f.close()
        self.path = path

    def delete(self):
        """
        Delete the file associated with this certificate.
        """
        if self.path:
            os.unlink(self.path)


class IdentityCertificate(Certificate):
    def __init__(self, alt_name=None, subject=None, **kwargs):
        Certificate.__init__(self, **kwargs)

        self.subject = subject
        self.alt_name = alt_name


class ProductCertificate1(Certificate):
    def __init__(self, products=None, **kwargs):
        Certificate.__init__(self, **kwargs)
        # The products in this certificate. The first is treated as the
        # primary or "marketing" product.
        if products is None:
            products = []
        self.products = products


class EntitlementCertificate1(ProductCertificate1):

    def __init__(self, order=None, content=None, **kwargs):
        ProductCertificate1.__init__(self, **kwargs)
        self.order = order
        self.content = content


# TODO: delete these if they're not needed:
class ProductCertificate2(Certificate):
    pass


class EntitlementCertificate2(Certificate):
    pass


class Product(object):
    """
    Represents the product information from a certificate.
    """
    def __init__(self, id=None, name=None, version=None, arch=None,
            provided_tags=None):
        self.id = id
        self.name = name
        self.version = version
        self.arch = arch
        self.provided_tags = provided_tags
        if self.provided_tags is None:
            self.provided_tags = []

    def __eq__(self, other):
        return (self.id == other.id)



class Order(object):
    """
    Represents the order information for the subscription an entitlement
    originated from.
    """

    def __init__(self, name=None, number=None, sku=None, subscription=None,
            quantity=None, virt_limit=None, socket_limit=None,
            contract_number=None, quantity_used=None, warning_period=None,
            account_number=None, provides_management=None, support_level=None,
            support_type=None, stacking_id=None, virt_only=None):

        self.name = name
        self.number = number # order number
        self.sku = sku
        self.subscription = subscription

        # This is the total quantity on the order:
        self.quantity = quantity

        self.virt_limit = virt_limit
        self.socket_limit = socket_limit
        self.contract_number = contract_number

        # The actual quantity used by this entitlement:
        self.quantity_used = quantity_used

        self.warning_period = warning_period
        self.account_number = account_number
        self.provides_management = provides_management
        self.support_level = support_level
        self.support_type = support_type
        self.stacking_id = stacking_id
        self.virt_only = virt_only

    def __str__(self):
        return "<Order: name=%s number=%s support_level=%s>" % \
                (self.name, self.number, self.support_level)


class Content(object):

    def __init__(self, name=None, label=None, quantity=None, flex_quantity=None,
            vendor=None, url=None, gpg=None, enabled=None, metadata_expire=None,
            required_tags=None):
        self.name = name
        self.label = label
        self.vendor = vendor
        self.url = url
        self.gpg = gpg

        if (enabled not in (None, 0, 1, "0", "1")):
            raise CertificateException("Invalid enabled setting: %s" % enabled)

        # Convert possible incoming None or string (0/1) to a boolean:
        # If enabled isn't specified in cert we assume True.
        self.enabled = True if \
                (enabled is None or enabled == "1" or enabled == True) \
                else False

        self.metadata_expire = metadata_expire
        self.required_tags = required_tags or []

        # Suspect both of these are unused:
        self.quantity = int(quantity) if quantity else None
        self.flex_quantity = int(flex_quantity) if flex_quantity else None

    def __eq__(self, other):
        return (self.label == other.label)

    def __str__(self):
        return "<Content: name=%s label=%s enabled=%s>" % \
                (self.name, self.label, self.enabled)


class CertificateException(Exception):
    pass

# Maps a major cert version to the class implementations to use for
# each certificate type:
# TODO: may not be needed if we can go to just one set of classes
VERSION_IMPLEMENTATIONS = {
    1: {
        ENTITLEMENT_CERT: EntitlementCertificate1,
        PRODUCT_CERT: ProductCertificate1,
    },
    2: {
        ENTITLEMENT_CERT: EntitlementCertificate2,
        PRODUCT_CERT: ProductCertificate2,
    },
}
