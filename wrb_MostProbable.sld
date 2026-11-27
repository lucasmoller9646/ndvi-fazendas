<?xml version="1.0" encoding="UTF-8"?>
<StyledLayerDescriptor xmlns="http://www.opengis.net/sld" xmlns:gml="http://www.opengis.net/gml" xmlns:sld="http://www.opengis.net/sld" version="1.0.0" xmlns:ogc="http://www.opengis.net/ogc">
  <UserLayer>
    <sld:LayerFeatureConstraints>
      <sld:FeatureTypeConstraint/>
    </sld:LayerFeatureConstraints>
    <sld:UserStyle>
      <sld:Name>MostProbable</sld:Name>
      <sld:FeatureTypeStyle>
        <sld:Rule>
          <sld:RasterSymbolizer>
            <sld:ChannelSelection>
              <sld:GrayChannel>
                <sld:SourceChannelName>1</sld:SourceChannelName>
              </sld:GrayChannel>
            </sld:ChannelSelection>
            <sld:ColorMap type="values">
              <sld:ColorMapEntry label="Acrisols" color="#9b1270" quantity="0"/>
              <sld:ColorMapEntry label="Albeluvisols" color="#ceab76" quantity="1"/>
              <sld:ColorMapEntry label="Alisols" color="#bc1f47" quantity="2"/>
              <sld:ColorMapEntry label="Andosols" color="#243d9a" quantity="3"/>
              <sld:ColorMapEntry label="Arenosols" color="#a91d99" quantity="4"/>
              <sld:ColorMapEntry label="Calcisols" color="#52da36" quantity="5"/>
              <sld:ColorMapEntry label="Cambisols" color="#8e3a23" quantity="6"/>
              <sld:ColorMapEntry label="Chernozems" color="#13bb91" quantity="7"/>
              <sld:ColorMapEntry label="Cryosols" color="#28a1ac" quantity="8"/>
              <sld:ColorMapEntry label="Durisols" color="#b15aeb" quantity="9"/>
              <sld:ColorMapEntry label="Ferralsols" color="#c08546" quantity="10"/>
              <sld:ColorMapEntry label="Fluvisols" color="#a2705c" quantity="11"/>
              <sld:ColorMapEntry label="Gleysols" color="#9a4812" quantity="12"/>
              <sld:ColorMapEntry label="Gypsisols" color="#e586f1" quantity="13"/>
              <sld:ColorMapEntry label="Histosols" color="#309d61" quantity="14"/>
              <sld:ColorMapEntry label="Kastanozems" color="#923deb" quantity="15"/>
              <sld:ColorMapEntry label="Leptosols" color="#7ccd5f" quantity="16"/>
              <sld:ColorMapEntry label="Lixisols" color="#c230d8" quantity="17"/>
              <sld:ColorMapEntry label="Luvisols" color="#ac4fc5" quantity="18"/>
              <sld:ColorMapEntry label="Nitisols" color="#cdb56c" quantity="19"/>
              <sld:ColorMapEntry label="Phaeozems" color="#e74c92" quantity="20"/>
              <sld:ColorMapEntry label="Planosols" color="#a975f6" quantity="21"/>
              <sld:ColorMapEntry label="Plinthosols" color="#639414" quantity="22"/>
              <sld:ColorMapEntry label="Podzols" color="#66f98b" quantity="23"/>
              <sld:ColorMapEntry label="Regosols" color="#158cb3" quantity="24"/>
              <sld:ColorMapEntry label="Solonchaks" color="#691d84" quantity="25"/>
              <sld:ColorMapEntry label="Solonetz" color="#673a83" quantity="26"/>
              <sld:ColorMapEntry label="Stagnosols" color="#74a3cf" quantity="27"/>
              <sld:ColorMapEntry label="Umbrisols" color="#83d991" quantity="28"/>
              <sld:ColorMapEntry label="Vertisols" color="#4b4688" quantity="29"/>
            </sld:ColorMap>
          </sld:RasterSymbolizer>
        </sld:Rule>
      </sld:FeatureTypeStyle>
    </sld:UserStyle>
  </UserLayer>
</StyledLayerDescriptor>
