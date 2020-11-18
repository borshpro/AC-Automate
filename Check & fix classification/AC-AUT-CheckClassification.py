#####################################################################
# This program deals with AC classification.
# 1. It reads all elements from AC, then their general properties (element ID & type) & elements classification.
# 2. Script insert data in postgreSQL DB so we can analyze it after in BI.
#####################################################################

# Set up globals
import sys
import json
import os
import psycopg2
import psycopg2.extras

from types import SimpleNamespace
from archicad import ACConnection

# Classes
class Element:
    def __init__(self, guid, eID, eType, className, classGUID, classSysGUID):
        self.guid = guid
        self.ID = eID
        self.type = eType
        self.className = className
        self.classGUID = classGUID
        self.classSysGUID = classSysGUID

class ClassificationItem:
    def __init__(self, guid, id, name, description):
        self.guid = guid
        self.id = id
        self.name = name
        self.description = description

# Functions
# General program configuration
def Config():
    sFilePath = os.path.dirname(os.path.abspath(__file__)) + '\\'+ 'config.json'
    with open(sFilePath) as json_file:
        objConfig = json.loads(json_file.read(), object_hook = lambda d: SimpleNamespace(**d))
    return objConfig

# PostgreSQL connection configuration
def PGConfig():
    sFilePath = os.path.dirname(os.path.abspath(__file__)) + '\\'+ '__NOTSYNC_postgresqlConfig.json'
    with open(sFilePath) as json_file:
        objPGConfig = json.loads(json_file.read(), object_hook = lambda d: SimpleNamespace(**d))
    return objPGConfig

# Recursive function for Classification system tree data flattening
def GetClassificationSystemItem(classificationItems,aClassificationSystemItems):
    if classificationItems.children is not None and len(classificationItems.children) != 0:
        for classificationItemChild in classificationItems.children:
            aClassificationSystemItems.append(
                ClassificationItem(
                    classificationItemChild.classificationItem.classificationItemId.guid,
                    classificationItemChild.classificationItem.id,
                    classificationItemChild.classificationItem.name,
                    classificationItemChild.classificationItem.description
                )
            )
            GetClassificationSystemItem(classificationItemChild.classificationItem,aClassificationSystemItems)

#####################################################################
# Main
def main(iArchiCADPort):
    # Define locals
    aElements = []
    aPropertyAllItems = []
    aPropertyGUID = []
    aElementsPropertyData = []
    aClassificationSystemItems = []
    objClassificationSystemID = []
    aACElementsDB = []
    aPropertyLocalListID = []

    # Try read configuration file
    try:
        objConfig = Config()
    except:
        print("Can't configure program")
    
    # Set internal connection with ArchiCAD
    try:
        conn = ACConnection.connect(int('19723')) 
        assert conn
        acc = conn.commands
    except:
        print("Can't connect to ArchiCAD")

    # Get all elements
    try:
        aElements = acc.GetAllElements()
    except:
        print("Can't get ArchiCAD elements")

    # Get properties data
    try:
        # Get all ArchiCAD properties names
        aPropertyAllItems = acc.GetAllPropertyNames()

        # Filter properties list to BuiltIn & properties defined in config.json
        # Just need to get Element ID & Element type
        aPropertyItems = list(filter(lambda p: p.type == 'BuiltIn' and p.nonLocalizedName in objConfig.aACPropertyName,aPropertyAllItems))

        # Iterate through filtered properties
        for pPropertyName in objConfig.aACPropertyName:
            aPropertyItem = [p for p in aPropertyItems if p.nonLocalizedName == pPropertyName]
            aPropertyLocalListID.append(aPropertyItems.index(aPropertyItem[0]))
        
        # Get filtered properties GUIDs
        aPropertyGUID = acc.GetPropertyIds(aPropertyItems)
    except:
        print("Can't get ArchiCAD properties")

    # Get elements properties data
    try:
        aElementsPropertyData = acc.GetPropertyValuesOfElements(aElements,aPropertyGUID)
    except:
        print("Can't get ArchiCAD elements properties data")
    
    # Get Classification system data
    try:
        # Get all Classification systems
        aClassificationSystems = acc.GetAllClassificationSystems()

        # Get specific classification system mentioned in config.json
        objClassificationSystem = next(c for c in aClassificationSystems if c.name == objConfig.sACClassificationName)

        # Get all Classification system items in tree
        tClassificationSystemItems = acc.GetAllClassificationsInSystem(objClassificationSystem.classificationSystemId)
    except:
        print("Can't get ArchiCAD Classification system data")

    # Flatten Classification system items for simplier usage
    try:
        for aClassificationSystemItem in tClassificationSystemItems:
            # First level for root Classification items 
            aClassificationSystemItems.append(
                ClassificationItem(
                    aClassificationSystemItem.classificationItem.classificationItemId.guid,
                    aClassificationSystemItem.classificationItem.id,
                    aClassificationSystemItem.classificationItem.name,
                    aClassificationSystemItem.classificationItem.description
                )
            )
            # Call recursive function for inner levels of items
            GetClassificationSystemItem(aClassificationSystemItem.classificationItem,aClassificationSystemItems)
    except:
        print("Can't flatten Classification system items")

    # Get elements classification
    try:
        # Make temporal list of Classification system IDs
        objClassificationSystemID.append(objClassificationSystem.classificationSystemId)

        # Get elements classification data
        aElementsnClassificationItem = acc.GetClassificationsOfElements(aElements, objClassificationSystemID)
    except:
        print("Can't get elements Classification data")

    # Set elements data - guid, id, type, classificationName, classificationGUID, classificationSystemGUID for insertion in DB
    # try:
    for iElementId,iElementProperty,iElementClassification in zip(aElements,aElementsPropertyData,aElementsnClassificationItem):
        # Get Classification item based on element classification
        iClassSystemItemTemp = next((f for f in aClassificationSystemItems if f.guid == iElementClassification.classificationIds[0].classificationId.classificationItemId.guid), None)
        
        # Check classification item
        if iClassSystemItemTemp is not None:
            iClassSystemItemTempID = iClassSystemItemTemp.id
            iClassSystemItemTempGUID = iClassSystemItemTemp.guid
        else:
            iClassSystemItemTempID = 'Unclassified'

        # Insert data in temp list 
        aACElementsDB.append(
            Element(
                iElementId.elementId.guid, 
                iElementProperty.propertyValues[aPropertyLocalListID[0]].propertyValue.value,
                iElementProperty.propertyValues[aPropertyLocalListID[1]].propertyValue.value,  
                iClassSystemItemTempID,
                iClassSystemItemTempGUID,
                iElementClassification.classificationIds[0].classificationId.classificationSystemId.guid
            )
        )
    # except:
    #     print("Can't set elements data for futher DB insertion")

    # Provide postgreSQL connection
    try:
        objPGConfig = PGConfig()
        pgConn = psycopg2.connect(
            database    = objPGConfig.database, 
            user        = objPGConfig.user, 
            password    = objPGConfig.password, 
            host        = objPGConfig.host
        )
        pgCur = pgConn.cursor()
        psycopg2.extras.register_uuid()
    except:
        print("Can't connect to DB")

    # Insert elements data in DB
    try:
        # Prepare table for insertion
        pgCur.execute("truncate table ac_classification_check")
        pgConn.commit()

        # Insert data
        for iACElement in aACElementsDB:
            pgExecuteResult = pgCur.execute(
                """INSERT INTO ac_classification_check ("elemGUID", "elemID", "elemType", "classGUID", "classType", "classSysGUID") VALUES (%s, %s, %s, %s, %s, %s);""",
                (iACElement.guid, iACElement.ID, iACElement.type, iACElement.classGUID, iACElement.className, iACElement.classSysGUID)
            )
            pgConn.commit()

        # Close connection
        pgCur.close()
        pgConn.close()
    except:
        print("Can't insert elements data in DB")

    return aACElementsDB

# Set up entry point
if __name__ == '__main__':
    # Init AC port
    iArchiCADPort = 0
    # Check args
    if len(sys.argv) >= 2:
        iArchiCADPort = sys.argv[1]
        pass
    else:
        # If args is empty set port with error value
        iArchiCADPort = -1
        pass
    main(iArchiCADPort)